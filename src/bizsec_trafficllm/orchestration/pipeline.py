"""Business -> Detection -> gated Attack-Type serial inference orchestration."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from bizsec_trafficllm.serialization import PromptSerializer, SerializationError
from bizsec_trafficllm.serialization.validation import MessageValidator
from bizsec_trafficllm.views import ViewEngine

from .protocols import AdapterBackend, RiskFusionBackend


PIPELINE_VERSION = "bizsec-serial-pipeline-v1"
FUSION_KEYS = {
    "risk_score",
    "risk_level",
    "evidence",
    "recommended_action",
}


class OrchestrationError(RuntimeError):
    """Raised when a backend or intermediate value violates the pipeline contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PipelineResultValidator:
    """Validate the final analysis result against the repository schema."""

    def __init__(self, schema_root: Path) -> None:
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError as exc:  # pragma: no cover
            raise OrchestrationError(
                "missing_dependency", "jsonschema is required for orchestration"
            ) from exc
        schemas = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in Path(schema_root).rglob("*.schema.json")
        ]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in schemas
            if "$id" in schema
        )
        target = next(
            (
                schema
                for schema in schemas
                if schema.get("$id", "").endswith(
                    "/pipeline/analysis_result.schema.json"
                )
            ),
            None,
        )
        if target is None:
            raise OrchestrationError(
                "pipeline_schema_missing", "analysis_result.schema.json was not found"
            )
        self._validator = Draft202012Validator(target, registry=registry)

    def validate(self, value: Mapping[str, Any]) -> None:
        errors = sorted(
            self._validator.iter_errors(value), key=lambda item: list(item.path)
        )
        if errors:
            error = errors[0]
            path = ".".join(str(token) for token in error.path) or "<root>"
            raise OrchestrationError(
                "pipeline_result_invalid", f"{path}: {error.message}"
            )


class SerialInferencePipeline:
    """Execute the three task contracts with a deterministic Detection gate."""

    def __init__(
        self,
        view_engine: ViewEngine,
        serializer: PromptSerializer,
        adapter_backend: AdapterBackend,
        risk_fusion_backend: RiskFusionBackend,
        schema_root: Path,
    ) -> None:
        self._view_engine = view_engine
        self._serializer = serializer
        self._adapter_backend = adapter_backend
        self._risk_fusion_backend = risk_fusion_backend
        self._message_validator = MessageValidator(Path(schema_root))
        self._result_validator = PipelineResultValidator(Path(schema_root))
        if not isinstance(adapter_backend.name, str) or not adapter_backend.name:
            raise OrchestrationError(
                "invalid_backend_name", "adapter backend name must be non-empty"
            )
        if not isinstance(risk_fusion_backend.name, str) or not risk_fusion_backend.name:
            raise OrchestrationError(
                "invalid_backend_name", "risk fusion backend name must be non-empty"
            )

    @staticmethod
    def _request_digest(request: Mapping[str, Any]) -> str:
        payload = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _predict(
        self,
        task: str,
        view: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            request = self._serializer.serialize_inference(view, task)
            raw_output = self._adapter_backend.predict(task, request)
        except SerializationError as exc:
            raise OrchestrationError(
                "inference_request_invalid", f"{task}: {exc}"
            ) from exc
        if not isinstance(raw_output, Mapping):
            raise OrchestrationError(
                "adapter_output_not_object", f"{task}: backend output must be an object"
            )
        output = dict(raw_output)
        try:
            self._message_validator.validate_target(task, output)
        except SerializationError as exc:
            raise OrchestrationError(
                "adapter_output_invalid", f"{task}: {exc}"
            ) from exc
        trace = {
            "stage": task,
            "sample_id": request["sample_id"],
            "view_version": view["view_version"],
            "request_message_version": request["message_version"],
            "request_sha256": self._request_digest(request),
            "business_prior": copy.deepcopy(view.get("priors", {}).get("business")),
            "output": copy.deepcopy(output),
        }
        return output, trace

    def run(
        self,
        sample: Mapping[str, Any],
        *,
        request_id: Optional[str] = None,
        security_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(sample, Mapping):
            raise OrchestrationError(
                "canonical_sample_invalid", "sample must be an object"
            )
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise OrchestrationError(
                "canonical_sample_invalid", "sample_id must be non-empty"
            )
        resolved_request_id = request_id or f"bizsec-{sample_id[:16]}"
        if not isinstance(resolved_request_id, str) or not resolved_request_id:
            raise OrchestrationError(
                "request_id_invalid", "request_id must be non-empty"
            )

        stages: List[Dict[str, Any]] = []
        business_view = self._view_engine.build_business(sample)
        business_output, business_trace = self._predict("business", business_view)
        stages.append(business_trace)

        detection_view = self._view_engine.build_detection(
            sample,
            business_prior=business_output,
            security_context=security_context,
        )
        detection_output, detection_trace = self._predict(
            "detection", detection_view
        )
        stages.append(detection_trace)

        attack_type_output: Optional[Dict[str, Any]] = None
        if detection_output["is_attack"]:
            attack_view = self._view_engine.build_attack_type(
                sample,
                business_prior=business_output,
                security_context=security_context,
            )
            attack_type_output, attack_trace = self._predict(
                "attack_type", attack_view
            )
            stages.append(attack_trace)

        fusion_value = self._risk_fusion_backend.fuse(
            business_output=copy.deepcopy(business_output),
            detection_output=copy.deepcopy(detection_output),
            attack_type_output=copy.deepcopy(attack_type_output),
        )
        if not isinstance(fusion_value, Mapping):
            raise OrchestrationError(
                "fusion_output_not_object", "risk fusion output must be an object"
            )
        fusion_output = dict(fusion_value)
        if set(fusion_output) != FUSION_KEYS:
            raise OrchestrationError(
                "fusion_output_keys_invalid",
                f"risk fusion output keys must be {sorted(FUSION_KEYS)}",
            )

        result = {
            "request_id": resolved_request_id,
            "business_type": business_output["business_type"],
            "is_attack": detection_output["is_attack"],
            "risk_score": fusion_output["risk_score"],
            "attack_type": (
                attack_type_output["attack_type"]
                if attack_type_output is not None
                else "benign"
            ),
            "risk_level": fusion_output["risk_level"],
            "evidence": fusion_output["evidence"],
            "recommended_action": fusion_output["recommended_action"],
            "model_backend": self._adapter_backend.name,
            "schema_version": "1.0",
        }
        self._result_validator.validate(result)
        return {
            "pipeline_version": PIPELINE_VERSION,
            "status": "passed",
            "request_id": resolved_request_id,
            "sample_id": sample_id,
            "adapter_backend": self._adapter_backend.name,
            "risk_fusion_backend": self._risk_fusion_backend.name,
            "gate": {
                "is_attack": detection_output["is_attack"],
                "attack_type_called": attack_type_output is not None,
            },
            "stages": stages,
            "result": result,
        }
