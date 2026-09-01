#!/usr/bin/env python3
"""Run real three-Adapter serial inference on a bounded Canonical v1 sample set."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bizsec_trafficllm.orchestration import (  # noqa: E402
    ChatGLM2AdapterBackend,
    DeterministicRiskFusionBackend,
    SerialInferencePipeline,
)
from bizsec_trafficllm.serialization import PromptSerializer  # noqa: E402
from bizsec_trafficllm.views import ViewEngine  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-file", type=Path, required=True)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--business-checkpoint", type=Path, required=True)
    parser.add_argument("--detection-checkpoint", type=Path, required=True)
    parser.add_argument("--attack-type-checkpoint", type=Path, required=True)
    parser.add_argument("--business-device", default="cuda:0")
    parser.add_argument("--detection-device", default="cuda:1")
    parser.add_argument("--attack-type-device", default="cuda:2")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def assert_v1_input(path: Path) -> None:
    resolved = path.resolve()
    parts = {part.lower() for part in resolved.parts}
    if any("v2" in part.lower() for part in resolved.parts):
        raise ValueError(f"refusing excluded v2 path: {path}")
    if "v1" not in parts:
        raise ValueError(f"canonical input must be under a v1 path: {path}")


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("pipeline output must be outside the Git repository")


def read_records(path: Path, start: int, count: int) -> Iterable[Dict[str, Any]]:
    if start < 0 or count <= 0:
        raise ValueError("start-index must be non-negative and sample-count positive")
    selected = []
    seen = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if seen >= start and len(selected) < count:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("canonical record must be an object")
                selected.append(value)
            seen += 1
            if len(selected) == count:
                break
    if len(selected) != count:
        raise ValueError(f"requested {count} records from index {start}, found {len(selected)}")
    return selected


def expected_targets(sample: Mapping[str, Any]) -> Mapping[str, Any]:
    labels = sample.get("labels") or {}
    targets = labels.get("targets") or {}
    return copy.deepcopy(targets)


def build_effect_summary(rows, failures, backend_calls) -> Dict[str, Any]:
    confusion = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    detection_evaluated = 0
    attack_type_evaluated = 0
    attack_type_correct = 0
    for row in rows:
        target_detection = row["expected"].get("detection")
        if isinstance(target_detection, Mapping):
            detection_evaluated += 1
            expected_attack = bool(target_detection["is_attack"])
            predicted_attack = bool(row["result"]["is_attack"])
            if expected_attack and predicted_attack:
                confusion["tp"] += 1
            elif expected_attack:
                confusion["fn"] += 1
            elif predicted_attack:
                confusion["fp"] += 1
            else:
                confusion["tn"] += 1
        target_attack_type = row["expected"].get("attack_type")
        if isinstance(target_attack_type, Mapping):
            attack_type_evaluated += 1
            if row["result"]["attack_type"] == target_attack_type["attack_type"]:
                attack_type_correct += 1
    detection_correct = confusion["tp"] + confusion["tn"]
    stage_summary = {}
    for task in ("business", "detection", "attack_type"):
        task_calls = [call for call in backend_calls if call["task"] == task]
        valid_calls = sum(1 for call in task_calls if call["schema_valid"])
        stage_summary[task] = {
            "calls": len(task_calls),
            "schema_valid": valid_calls,
            "schema_valid_rate": valid_calls / len(task_calls) if task_calls else None,
            "source_truncated": sum(
                1 for call in task_calls if call["source_truncated"]
            ),
        }
    return {
        "status": "passed" if not failures else "completed_with_failures",
        "scope": "bounded 100-step Adapter effect probe; not a formal evaluation",
        "samples_requested": len(rows) + len(failures),
        "pipeline_successes": len(rows),
        "pipeline_failures": len(failures),
        "pipeline_success_rate": (
            len(rows) / (len(rows) + len(failures)) if rows or failures else None
        ),
        "detection": {
            "evaluated": detection_evaluated,
            "correct": detection_correct,
            "accuracy": (
                detection_correct / detection_evaluated if detection_evaluated else None
            ),
            "confusion": confusion,
        },
        "attack_type_end_to_end": {
            "evaluated_attack_samples": attack_type_evaluated,
            "correct": attack_type_correct,
            "accuracy": (
                attack_type_correct / attack_type_evaluated
                if attack_type_evaluated
                else None
            ),
        },
        "adapter_calls": {
            task: sum(1 for call in backend_calls if call["task"] == task)
            for task in ("business", "detection", "attack_type")
        },
        "adapter_stage_summary": stage_summary,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    assert_v1_input(args.canonical_file)
    assert_external_output(args.output_dir)
    samples = list(read_records(args.canonical_file, args.start_index, args.sample_count))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"real-serial-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)

    view_engine = ViewEngine(
        PROJECT_ROOT / "schemas",
        load_json(PROJECT_ROOT / "configs/views/representation_selection_v1.json"),
        load_json(PROJECT_ROOT / "configs/views/token_budget_v1.json"),
    )
    serializer = PromptSerializer(
        PROJECT_ROOT / "schemas",
        load_json(PROJECT_ROOT / "configs/serialization/prompt_templates_v1.json"),
    )
    backend = ChatGLM2AdapterBackend.from_checkpoints(
        PROJECT_ROOT,
        args.model_dir,
        {
            "business": args.business_checkpoint,
            "detection": args.detection_checkpoint,
            "attack_type": args.attack_type_checkpoint,
        },
        {
            "business": args.business_device,
            "detection": args.detection_device,
            "attack_type": args.attack_type_device,
        },
    )
    pipeline = SerialInferencePipeline(
        view_engine,
        serializer,
        backend,
        DeterministicRiskFusionBackend(),
        PROJECT_ROOT / "schemas",
    )
    rows = []
    failures = []
    try:
        for offset, sample in enumerate(samples):
            inference_sample = copy.deepcopy(sample)
            inference_sample["labels"] = None
            try:
                run = pipeline.run(
                    inference_sample,
                    request_id=f"real-{args.start_index + offset}-{sample['sample_id'][:12]}",
                    security_context={"rule_hits": [], "threat_intel_hit": None},
                )
                rows.append(
                    {
                        "index": args.start_index + offset,
                        "sample_id": sample["sample_id"],
                        "expected": expected_targets(sample),
                        "gate": run["gate"],
                        "stages": run["stages"],
                        "result": run["result"],
                    }
                )
            except Exception as exc:
                failures.append(
                    {
                        "index": args.start_index + offset,
                        "sample_id": sample.get("sample_id"),
                        "error_type": type(exc).__name__,
                        "error_code": getattr(exc, "code", None),
                        "message": str(exc),
                    }
                )
        summary = build_effect_summary(rows, failures, backend.calls)
        summary.update(
            {
                "canonical_file": str(args.canonical_file.resolve()),
                "start_index": args.start_index,
                "labels_removed_before_inference": True,
                "adapter_backend": backend.name,
                "risk_fusion_backend": DeterministicRiskFusionBackend.name,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "run_dir": str(run_dir),
            }
        )
        write_json(run_dir / "effect-summary.json", summary)
        write_json(run_dir / "pipeline-runs.json", rows)
        write_json(run_dir / "pipeline-failures.json", failures)
        write_json(run_dir / "backend-calls.json", backend.calls)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"run_dir={run_dir}")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
