#!/usr/bin/env python3
"""Aggregate fixed-sample large-only Business checkpoint evaluations."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


STEP_RE = re.compile(r"step-(\d{6})$")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--baseline-summary", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def compact_metrics(summary: dict) -> dict:
    return {
        "samples": int(summary["samples"]),
        "schema_valid_rate": float(summary["schema_valid_rate"]),
        "accuracy": float(summary["primary_label_accuracy"]),
        "macro_f1": float(summary["multiclass"]["macro_f1"]),
        "source_truncated": int(summary.get("source_truncated", 0)),
    }


def main() -> None:
    args = parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    baseline_summary = load_json(args.baseline_summary)
    baseline_rows = load_json(args.baseline_summary.with_name("evaluation-rows.json"))
    baseline_ids = [row["sample_id"] for row in baseline_rows]

    curve = []
    reference_ids = None
    for step_dir in sorted(args.validation_root.glob("step-*")):
        match = STEP_RE.match(step_dir.name)
        if match is None:
            continue
        summaries = list(step_dir.glob("business-*/evaluation-summary.json"))
        if len(summaries) != 1:
            raise RuntimeError(
                f"expected one summary under {step_dir}, found {len(summaries)}"
            )
        summary_path = summaries[0]
        rows_path = summary_path.with_name("evaluation-rows.json")
        summary = load_json(summary_path)
        rows = load_json(rows_path)
        sample_ids = [row["sample_id"] for row in rows]
        if reference_ids is None:
            reference_ids = sample_ids
        item = {
            "step": int(match.group(1)),
            **compact_metrics(summary),
            "same_ordered_sample_ids_as_first_checkpoint": sample_ids == reference_ids,
            "same_ordered_sample_ids_as_old_baseline": sample_ids == baseline_ids,
            "summary_path": str(summary_path),
            "checkpoint_path": summary["checkpoint"]["path"],
            "checkpoint_sha256": summary["checkpoint"]["sha256"],
            "by_dataset": {
                dataset_id: compact_metrics(dataset_summary)
                for dataset_id, dataset_summary in sorted(summary["by_dataset"].items())
            },
        }
        curve.append(item)

    if len(curve) != 20:
        raise RuntimeError(f"expected 20 checkpoint summaries, found {len(curve)}")
    best = max(curve, key=lambda item: (item["macro_f1"], item["accuracy"]))
    aggregate = {
        "status": "passed",
        "comparison": "single-seed descriptive checkpoint curve",
        "baseline": {
            **compact_metrics(baseline_summary),
            "summary_path": str(args.baseline_summary),
        },
        "sample_identity": {
            "all_checkpoints_match_first": all(
                item["same_ordered_sample_ids_as_first_checkpoint"] for item in curve
            ),
            "all_checkpoints_match_old_baseline": all(
                item["same_ordered_sample_ids_as_old_baseline"] for item in curve
            ),
            "ordered_sample_count": len(baseline_ids),
        },
        "best_checkpoint": best,
        "curve": curve,
    }
    json_path = args.output_root / "checkpoint-curve-summary.json"
    json_path.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    csv_path = args.output_root / "checkpoint-curve.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "step",
                "samples",
                "schema_valid_rate",
                "accuracy",
                "macro_f1",
                "source_truncated",
                "same_ordered_sample_ids_as_old_baseline",
                "checkpoint_sha256",
            ),
        )
        writer.writeheader()
        for item in curve:
            writer.writerow({field: item[field] for field in writer.fieldnames})
    print(json.dumps({
        "status": aggregate["status"],
        "checkpoints": len(curve),
        "sample_identity": aggregate["sample_identity"],
        "baseline": aggregate["baseline"],
        "best_step": best["step"],
        "best_accuracy": best["accuracy"],
        "best_macro_f1": best["macro_f1"],
        "json_path": str(json_path),
        "csv_path": str(csv_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
