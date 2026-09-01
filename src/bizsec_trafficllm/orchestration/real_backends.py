"""Production-oriented backends for real PrefixEncoder serial inference."""

from __future__ import annotations

import copy
import gc
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from bizsec_trafficllm.inference import ChatGLM2InferenceInterface


TASKS = ("business", "detection", "attack_type")


class AdapterBackendError(RuntimeError):
    """Raised when a real task Adapter cannot produce a validated output."""


class ChatGLM2AdapterBackend:
    """Keep one real task Adapter per device and expose the pipeline protocol."""

    name = "chatglm2-prefix-adapters-v1"

    def __init__(self, interfaces: Mapping[str, Any]) -> None:
        if set(interfaces) != set(TASKS):
            raise AdapterBackendError(
                f"interfaces must contain exactly {list(TASKS)}"
            )
        self._interfaces = dict(interfaces)
        self.calls = []

    @classmethod
    def from_checkpoints(
        cls,
        project_root: Path,
        model_dir: Path,
        checkpoints: Mapping[str, Path],
        devices: Mapping[str, str],
    ) -> "ChatGLM2AdapterBackend":
        if set(checkpoints) != set(TASKS):
            raise AdapterBackendError(
                f"checkpoints must contain exactly {list(TASKS)}"
            )
        if set(devices) != set(TASKS):
            raise AdapterBackendError(f"devices must contain exactly {list(TASKS)}")
        interfaces: Dict[str, Any] = {}
        try:
            for task in TASKS:
                interfaces[task] = ChatGLM2InferenceInterface.from_adapter_checkpoint(
                    project_root,
                    task,
                    model_dir,
                    checkpoints[task],
                    Path(project_root) / "schemas",
                    devices[task],
                )
        except Exception:
            interfaces.clear()
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:  # pragma: no cover
                pass
            raise
        return cls(interfaces)

    def predict(self, task: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if task not in self._interfaces:
            raise AdapterBackendError(f"unsupported task: {task!r}")
        if request.get("task") != task:
            raise AdapterBackendError(
                f"request task mismatch: {request.get('task')!r} != {task!r}"
            )
        interface = self._interfaces[task]
        checkpoint = interface.adapter_checkpoint or {}
        metadata = checkpoint.get("training_metadata") or {}
        source_length = metadata.get("max_source_length")
        target_length = metadata.get("max_target_length")
        if not isinstance(source_length, int) or source_length <= 0:
            raise AdapterBackendError(f"{task} checkpoint lacks max_source_length")
        if not isinstance(target_length, int) or target_length <= 0:
            raise AdapterBackendError(f"{task} checkpoint lacks max_target_length")
        result = interface.predict(
            request,
            max_source_length=source_length,
            max_length=source_length + target_length + 1,
        )
        diagnostic = {
            "task": task,
            "sample_id": request.get("sample_id"),
            "checkpoint_sha256": checkpoint.get("sha256"),
            "device": interface.device,
            "inference_seconds": result.get("inference_seconds"),
            "source_tokens_raw": result.get("source_tokens_raw"),
            "source_tokens_used": result.get("source_tokens_used"),
            "source_truncated": result.get("source_truncated"),
            "raw_model_output": result.get("raw_model_output"),
            "parsed_output": copy.deepcopy(result.get("parsed_output")),
            "schema_valid": result.get("schema_valid"),
            "schema_error": result.get("schema_error"),
            "json_parse_error": result.get("json_parse_error"),
        }
        self.calls.append(diagnostic)
        if not result.get("schema_valid"):
            reason = result.get("schema_error") or result.get("json_parse_error")
            raise AdapterBackendError(
                f"{task} Adapter returned an invalid output: {reason}; "
                f"raw={result.get('raw_model_output')!r}"
            )
        parsed_output = result.get("parsed_output")
        if not isinstance(parsed_output, Mapping):
            raise AdapterBackendError(f"{task} parsed output is not an object")
        return copy.deepcopy(dict(parsed_output))

    def close(self) -> None:
        """Release model references and cached CUDA allocations."""

        self._interfaces.clear()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:  # pragma: no cover
            pass


class DeterministicRiskFusionBackend:
    """Auditable first-version fusion until a calibrated Fusion Adapter exists."""

    name = "deterministic-risk-fusion-v1"

    _ATTACK_RISK = {
        "web_attack": (0.75, "high", ["block_source", "inspect_http_logs"]),
        "apt": (0.95, "critical", ["isolate_asset", "start_incident_response"]),
        "malicious_doh": (0.80, "high", ["block_doh_endpoint", "inspect_dns_logs"]),
        "botnet": (0.85, "high", ["isolate_asset", "inspect_command_and_control"]),
        "malware": (0.90, "critical", ["isolate_asset", "run_malware_analysis"]),
        "unknown_attack": (0.65, "medium", ["increase_monitoring", "manual_review"]),
    }

    def fuse(
        self,
        *,
        business_output: Mapping[str, Any],
        detection_output: Mapping[str, Any],
        attack_type_output: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        business_evidence = (
            f"business={business_output['business_domain']}/"
            f"{business_output['business_type']}"
        )
        if not detection_output["is_attack"]:
            return {
                "risk_score": 0.05,
                "risk_level": "low",
                "evidence": [business_evidence, "detection.is_attack=false"],
                "recommended_action": ["continue_monitoring"],
            }
        if attack_type_output is None:
            raise AdapterBackendError(
                "attack_type_output is required when detection.is_attack=true"
            )
        attack_type = attack_type_output["attack_type"]
        try:
            score, level, actions = self._ATTACK_RISK[attack_type]
        except KeyError as exc:
            raise AdapterBackendError(f"unsupported attack_type: {attack_type!r}") from exc
        evidence = [
            business_evidence,
            "detection.is_attack=true",
            f"attack_type={attack_type}",
        ]
        attack_family = attack_type_output.get("attack_family")
        if attack_family:
            evidence.append(f"attack_family={attack_family}")
        return {
            "risk_score": score,
            "risk_level": level,
            "evidence": evidence,
            "recommended_action": list(actions),
        }
