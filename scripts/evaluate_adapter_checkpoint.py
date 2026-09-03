#!/usr/bin/env python3
"""Evaluate one Adapter on a balanced validation subset or a complete v1 split."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bizsec_trafficllm.evaluation import (  # noqa: E402
    iter_test_records,
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
    parser.add_argument(
        "--partition", choices=("validation", "test"), default="validation"
    )
    parser.add_argument(
        "--selection-strategy",
        choices=("label-balanced", "all"),
        default="label-balanced",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--log-every", type=int, default=100)
    parser.add_argument(
        "--include-dataset",
        action="append",
        dest="included_datasets",
        metavar="DATASET_ID",
        help=(
            "restrict evaluation to one Messages v1 dataset; repeat this option "
            "to include multiple datasets"
        ),
    )
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
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be positive")
    if args.log_every <= 0:
        raise ValueError("log-every must be positive")
    if args.partition == "test" and args.selection_strategy != "all":
        raise ValueError("test partition must use --selection-strategy all")
    if args.selection_strategy == "all" and args.limit is not None:
        raise ValueError("--limit cannot be combined with --selection-strategy all")
    assert_external_output(args.output_dir)
    config = load_json(
        PROJECT_ROOT / "configs" / "training" / f"{args.task}_ptuning_v2.json"
    )
    validation = config["validation"]
    messages_root = PROJECT_ROOT / config["messages_root"]
    included_datasets = (
        sorted(set(args.included_datasets)) if args.included_datasets else None
    )
    if included_datasets:
        print(
            f"[{args.task}] included_datasets={','.join(included_datasets)}",
            flush=True,
        )
    if args.partition == "validation":
        source_records = iter_partition_records(
            messages_root,
            args.task,
            float(validation["fraction"]),
            int(validation["seed"]),
            partition="validation",
            included_dataset_ids=included_datasets,
        )
    else:
        source_records = iter_test_records(
            messages_root,
            args.task,
            included_dataset_ids=included_datasets,
        )
    if args.selection_strategy == "label-balanced":
        records = select_balanced_records(
            args.task,
            source_records,
            args.limit if args.limit is not None else 50,
            int(validation["seed"]),
        )
    else:
        records = list(source_records)
        if not records:
            raise ValueError(f"no records found in {args.partition} partition")
    print(
        f"[{args.task}] evaluation_records={len(records)} "
        f"partition={args.partition} selection={args.selection_strategy}",
        flush=True,
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
    schema_valid_count = 0
    started = time.monotonic()
    for index, record in enumerate(records, start=1):
        record_metadata = record.get("metadata") or {}
        dataset_id = record_metadata.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError(
                f"record is missing metadata.dataset_id: {record.get('sample_id')}"
            )
        request, expected = training_record_to_inference(
            record,
            evaluation_partition=(
                "deterministic_validation"
                if args.partition == "validation"
                else "held_out_test"
            ),
        )
        result = interface.predict(
            request,
            max_source_length=source_length,
            max_length=source_length + target_length + 1,
        )
        rows.append(
            {
                "sample_id": record["sample_id"],
                "dataset_id": dataset_id,
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
        schema_valid_count += bool(result["schema_valid"])
        if index == 1 or index % args.log_every == 0 or index == len(records):
            elapsed = time.monotonic() - started
            rate = index / elapsed if elapsed else 0.0
            remaining = (len(records) - index) / rate if rate else 0.0
            print(
                f"[{args.task}] evaluated={index}/{len(records)} "
                f"schema_valid={schema_valid_count}/{index} elapsed={elapsed:.1f}s "
                f"eta={remaining:.1f}s",
                flush=True,
            )
    summary = summarize_adapter_predictions(args.task, rows)
    grouped_rows = {}
    for row in rows:
        grouped_rows.setdefault(row["dataset_id"], []).append(row)
    summary["by_dataset"] = {
        dataset_id: summarize_adapter_predictions(args.task, dataset_rows)
        for dataset_id, dataset_rows in sorted(grouped_rows.items())
    }
    label_key = {
        "business": "business_type",
        "detection": "is_attack",
        "attack_type": "attack_type",
    }[args.task]
    summary.update(
        {
            "status": "passed",
            "scope": {
                ("validation", "label-balanced"): (
                    "fixed deterministic label-balanced validation subset; "
                    "checkpoint selection only, not final test evaluation"
                ),
                ("validation", "all"): (
                    "complete deterministic validation partition; "
                    "not final test evaluation"
                ),
                ("test", "all"): (
                    "complete held-out Messages v1 test split; final evaluation "
                    "for a frozen checkpoint"
                ),
            }[(args.partition, args.selection_strategy)],
            "task": args.task,
            "partition": args.partition,
            "validation_fraction": validation["fraction"],
            "validation_seed": validation["seed"],
            "selection_strategy": (
                "deterministic_label_round_robin"
                if args.selection_strategy == "label-balanced"
                else "all_records"
            ),
            "dataset_filter": {
                "included_dataset_ids": included_datasets,
                "mode": "include" if included_datasets else "all",
            },
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
