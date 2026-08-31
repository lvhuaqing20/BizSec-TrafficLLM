#!/usr/bin/env python3
"""Validate both serial-pipeline gate branches with scripted adapter decisions."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bizsec_trafficllm.orchestration import SerialInferencePipeline  # noqa: E402
from bizsec_trafficllm.serialization import PromptSerializer  # noqa: E402
from bizsec_trafficllm.views import ViewEngine  # noqa: E402


SCENARIOS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "benign": {
        "business": {
            "business_domain": "application",
            "business_type": "validation_service",
        },
        "detection": {"is_attack": False},
        "attack_type": {"attack_type": "web_attack", "attack_family": None},
    },
    "attack": {
        "business": {
            "business_domain": "application",
            "business_type": "validation_service",
        },
        "detection": {"is_attack": True},
        "attack_type": {"attack_type": "web_attack", "attack_family": None},
    },
}


class ScriptedValidationAdapterBackend:
    """Test-only backend that returns schema-valid, predeclared decisions."""

    name = "scripted-validation-adapter-v1"

    def __init__(self, outputs: Mapping[str, Mapping[str, Any]]) -> None:
        self._outputs = copy.deepcopy(dict(outputs))
        self.calls = []

    def predict(self, task: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(
            {"task": task, "sample_id": request.get("sample_id")}
        )
        try:
            return copy.deepcopy(self._outputs[task])
        except KeyError as exc:
            raise RuntimeError(f"no scripted output for task: {task}") from exc


class ValidationRiskFusionBackend:
    """Test-only deterministic fusion for control-flow validation."""

    name = "deterministic-validation-fusion-v1"

    def fuse(
        self,
        *,
        business_output: Mapping[str, Any],
        detection_output: Mapping[str, Any],
        attack_type_output: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        business_evidence = f"business_type={business_output['business_type']}"
        if not detection_output["is_attack"]:
            return {
                "risk_score": 0.05,
                "risk_level": "low",
                "evidence": [business_evidence, "detection.is_attack=false"],
                "recommended_action": ["continue_monitoring"],
            }
        if attack_type_output is None:
            raise RuntimeError("attack_type_output is required for attack traffic")
        attack_type = attack_type_output["attack_type"]
        score_level = {
            "web_attack": (0.75, "high"),
            "apt": (0.95, "critical"),
            "malicious_doh": (0.80, "high"),
            "botnet": (0.85, "high"),
            "malware": (0.90, "critical"),
            "unknown_attack": (0.65, "medium"),
        }
        score, level = score_level[attack_type]
        return {
            "risk_score": score,
            "risk_level": level,
            "evidence": [
                business_evidence,
                "detection.is_attack=true",
                f"attack_type={attack_type}",
            ],
            "recommended_action": ["block_source", "inspect_security_logs"],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=tuple(SCENARIOS))
    parser.add_argument("--canonical-file", type=Path, required=True)
    parser.add_argument("--sample-index", type=int, default=0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--request-id")
    return parser.parse_args()


def assert_v1_input(path: Path) -> None:
    resolved = path.resolve()
    if any("v2" in part.lower() for part in resolved.parts):
        raise ValueError(f"refusing excluded v2 path: {path}")
    if "v1" not in {part.lower() for part in resolved.parts}:
        raise ValueError(f"canonical input must be under a v1 path: {path}")


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("orchestration output must be outside the Git repository")


def read_record(path: Path, index: int) -> Dict[str, Any]:
    if index < 0:
        raise ValueError("sample-index must be non-negative")
    seen = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if seen == index:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("canonical record must be an object")
                return value
            seen += 1
    raise ValueError(f"sample-index {index} is outside {path}")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()
    assert_v1_input(args.canonical_file)
    assert_external_output(args.output_dir)
    sample = read_record(args.canonical_file, args.sample_index)
    inference_sample = copy.deepcopy(sample)
    inference_sample["labels"] = None

    view_engine = ViewEngine(
        PROJECT_ROOT / "schemas",
        load_json(PROJECT_ROOT / "configs/views/representation_selection_v1.json"),
        load_json(PROJECT_ROOT / "configs/views/token_budget_v1.json"),
    )
    serializer = PromptSerializer(
        PROJECT_ROOT / "schemas",
        load_json(PROJECT_ROOT / "configs/serialization/prompt_templates_v1.json"),
    )
    adapter_backend = ScriptedValidationAdapterBackend(SCENARIOS[args.scenario])
    pipeline = SerialInferencePipeline(
        view_engine,
        serializer,
        adapter_backend,
        ValidationRiskFusionBackend(),
        PROJECT_ROOT / "schemas",
    )
    run = pipeline.run(
        inference_sample,
        request_id=args.request_id,
        security_context={"rule_hits": [], "threat_intel_hit": None},
    )
    expected_calls = (
        ["business", "detection", "attack_type"]
        if args.scenario == "attack"
        else ["business", "detection"]
    )
    actual_calls = [item["task"] for item in adapter_backend.calls]
    if actual_calls != expected_calls:
        raise RuntimeError(
            f"unexpected adapter call order: {actual_calls} != {expected_calls}"
        )

    run.update(
        {
            "validation_scope": (
                "real Canonical v1 input with scripted Adapter/Fusion outputs; "
                "orchestration validation only"
            ),
            "scenario": args.scenario,
            "canonical_file": str(args.canonical_file.resolve()),
            "canonical_labels_removed": True,
            "backend_calls": adapter_backend.calls,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"{args.scenario}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    result_path = run_dir / "analysis-result.json"
    trace_path = run_dir / "pipeline-trace.json"
    result_path.write_text(
        json.dumps(run["result"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path.write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(run["result"], ensure_ascii=False, indent=2))
    print(f"result_path={result_path}")
    print(f"trace_path={trace_path}")


if __name__ == "__main__":
    main()
