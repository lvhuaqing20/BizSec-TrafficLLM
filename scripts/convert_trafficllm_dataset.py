#!/usr/bin/env python3
"""Convert TrafficLLM JSONL files into canonical samples with failure audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bizsec_trafficllm.data import DatasetConverter  # noqa: E402


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, default=PROJECT_ROOT / "schemas")
    parser.add_argument("--config-root", type=Path, default=PROJECT_ROOT / "configs" / "canonical")
    parser.add_argument("--registry", type=Path, default=PROJECT_ROOT / "configs" / "labels" / "label_registry_v1.json")
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--split", action="append", choices=["train", "test", "all"], default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-per-label", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.sample_per_label is not None and args.sample_per_label <= 0:
        parser.error("--sample-per-label must be positive")
    if args.limit is not None and args.sample_per_label is not None:
        parser.error("--limit and --sample-per-label are mutually exclusive")

    converter = DatasetConverter(
        data_root=args.data_root,
        schema_root=args.schema_root,
        source_mapping=load_json(args.config_root / "source_mapping_v1.json"),
        label_registry=load_json(args.registry),
        privacy_policy=load_json(args.config_root / "privacy_policy_v1.json"),
    )
    dataset_ids = converter.dataset_ids if not args.dataset or "all" in args.dataset else args.dataset
    unknown = set(dataset_ids) - set(converter.dataset_ids)
    if unknown:
        parser.error(f"unknown datasets: {sorted(unknown)}")
    splits = ["train", "test"] if not args.split or "all" in args.split else list(dict.fromkeys(args.split))
    report = converter.convert_many(
        dataset_ids,
        splits,
        args.output_dir,
        limit=args.limit,
        sample_per_label=args.sample_per_label,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
