#!/usr/bin/env python3
"""Evaluate one real Adapter checkpoint on a fixed Messages v1 validation subset."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bizsec_trafficllm.evaluation import (  # noqa: E402
    select_balanced_records,
    summarize_adapter_predictions,
    training_record_to_inference,
)
from bizsec_trafficllm.inference import ChatGLM2InferenceInterface  # noqa: E402
from bizsec_trafficllm.training import iter_partition_records  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=("business", "detection", "attack_type"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--limit", type=int, default=50)
    return parser.parse_args()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("evaluation output must be outside the Git repository")


def main() -> None:
    args = parse_args()
    if args.limit <= 0:
        raise ValueError("limit must be positive")
    assert_external_output(args.output_dir)
    config = load_json(
        PROJECT_ROOT / "configs" / "training" / f"{args.task}_ptuning_v2.json"
    )
    validation = config["validation"]
    validation_records = iter_partition_records(
        PROJECT_ROOT / config["messages_root"],
        args.task,
        float(validation["fraction"]),
        int(validation["seed"]),
        partition="validation",
    )
    records = select_balanced_records(
        args.task,
        validation_records,
        args.limit,
        int(validation["seed"]),
    )
    interface = ChatGLM2InferenceInterface.from_adapter_checkpoint(
        PROJECT_ROOT,
        args.task,
        args.model_dir,
        args.checkpoint,
        PROJECT_ROOT / "schemas",
        args.device,
    )
    metadata = interface.adapter_checkpoint["training_metadata"]
    source_length = int(metadata["max_source_length"])
    target_length = int(metadata["max_target_length"])
    rows = []
    for record in records:
        request, expected = training_record_to_inference(record)
        result = interface.predict(
            request,
            max_source_length=source_length,
            max_length=source_length + target_length + 1,
        )
        rows.append(
            {
                "sample_id": record["sample_id"],
                "expected": expected,
                "prediction": result["parsed_output"],
                "raw_model_output": result["raw_model_output"],
                "schema_valid": result["schema_valid"],
                "schema_error": result["schema_error"],
                "json_parse_error": result["json_parse_error"],
                "source_tokens_raw": result["source_tokens_raw"],
                "source_tokens_used": result["source_tokens_used"],
                "source_truncated": result["source_truncated"],
                "inference_seconds": result["inference_seconds"],
            }
        )
    summary = summarize_adapter_predictions(args.task, rows)
    label_key = {
        "business": "business_type",
        "detection": "is_attack",
        "attack_type": "attack_type",
    }[args.task]
    summary.update(
        {
            "status": "passed",
            "scope": (
                "fixed deterministic label-balanced validation subset; "
                "not final test evaluation"
            ),
            "task": args.task,
            "partition": "validation",
            "validation_fraction": validation["fraction"],
            "validation_seed": validation["seed"],
            "selection_strategy": "deterministic_label_round_robin",
            "expected_label_distribution": dict(
                sorted(
                    Counter(str(row["expected"][label_key]) for row in rows).items()
                )
            ),
            "checkpoint": interface.adapter_checkpoint,
            "device": args.device,
            "source_truncated": sum(row["source_truncated"] for row in rows),
            "mean_inference_seconds": sum(row["inference_seconds"] for row in rows) / len(rows),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"{args.task}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "evaluation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "evaluation-rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"run_dir={run_dir}")


if __name__ == "__main__":
    main()
