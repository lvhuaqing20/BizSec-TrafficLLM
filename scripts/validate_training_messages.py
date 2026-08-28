#!/usr/bin/env python3
"""Independently validate messages against schemas and their source task Views."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bizsec_trafficllm.serialization import PromptSerializer, SerializationError  # noqa: E402


FORBIDDEN_USER_KEYS = {
    "labels",
    "raw_label",
    "ground_truth",
    "target",
    "output",
    "instruction",
    "candidate_labels",
    "confidence",
    "decision_source",
    "evidence_codes",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--source-view-dir", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, required=True)
    parser.add_argument("--prompt-config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-business", type=int)
    parser.add_argument("--expected-detection", type=int)
    parser.add_argument("--expected-attack-type", type=int)
    parser.add_argument("--limit-per-file", type=int)
    args = parser.parse_args()
    if args.limit_per_file is not None and args.limit_per_file <= 0:
        parser.error("--limit-per-file must be positive")

    serializer = PromptSerializer(args.schema_root, load_json(args.prompt_config))
    message_root = args.input_dir / "examples"
    source_root = args.source_view_dir / "examples"
    message_files = {
        path.relative_to(message_root): path for path in sorted(message_root.rglob("*.jsonl"))
    }
    source_files = {
        path.relative_to(source_root): path for path in sorted(source_root.rglob("*.jsonl"))
    }
    errors = []
    missing_outputs = sorted(set(source_files) - set(message_files))
    unexpected_outputs = sorted(set(message_files) - set(source_files))
    if missing_outputs:
        errors.append(f"missing output files: {[str(item) for item in missing_outputs[:10]]}")
    if unexpected_outputs:
        errors.append(f"unexpected output files: {[str(item) for item in unexpected_outputs[:10]]}")

    counts = Counter()
    split_counts = Counter()
    task_split_counts = Counter()
    pairs = set()
    duplicate_pairs = 0
    digest = hashlib.sha256()
    for relative in sorted(set(message_files) & set(source_files)):
        task, dataset_id, filename = relative.parts
        split = Path(filename).stem
        with message_files[relative].open("rb") as messages, source_files[relative].open(
            "rb"
        ) as sources:
            message_lines = (
                itertools.islice(messages, args.limit_per_file)
                if args.limit_per_file is not None
                else messages
            )
            source_lines = (
                itertools.islice(sources, args.limit_per_file)
                if args.limit_per_file is not None
                else sources
            )
            for line_number, (message_raw, source_raw) in enumerate(
                itertools.zip_longest(message_lines, source_lines), 1
            ):
                if message_raw is None or source_raw is None:
                    errors.append(f"{relative}:{line_number}: source/output record count differs")
                    continue
                digest.update(message_raw)
                try:
                    message = json.loads(message_raw)
                    source = json.loads(source_raw)
                except json.JSONDecodeError as exc:
                    errors.append(f"{relative}:{line_number}: invalid JSON: {exc}")
                    continue
                try:
                    expected = serializer.serialize_training(source, dataset_id, split)
                except SerializationError as exc:
                    errors.append(f"{relative}:{line_number}: source {exc.code}: {exc}")
                    continue
                if message != expected:
                    errors.append(f"{relative}:{line_number}: message differs from deterministic source serialization")
                try:
                    user_view = json.loads(message["messages"][1]["content"])
                    assistant_target = json.loads(message["messages"][2]["content"])
                except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                    errors.append(f"{relative}:{line_number}: invalid message content: {exc}")
                    continue
                if user_view != source.get("view"):
                    errors.append(f"{relative}:{line_number}: user content is not the source View")
                if assistant_target != source.get("target"):
                    errors.append(f"{relative}:{line_number}: assistant content is not the source target")
                leaked = set(walk_keys(user_view)) & FORBIDDEN_USER_KEYS
                if leaked:
                    errors.append(f"{relative}:{line_number}: forbidden user keys {sorted(leaked)}")
                pair = (message.get("sample_id"), message.get("task"))
                if pair in pairs:
                    duplicate_pairs += 1
                    errors.append(f"{relative}:{line_number}: duplicate sample/task pair {pair}")
                pairs.add(pair)
                counts[task] += 1
                split_counts[split] += 1
                task_split_counts[f"{task}:{split}"] += 1

    expected_counts = {
        "business": args.expected_business,
        "detection": args.expected_detection,
        "attack_type": args.expected_attack_type,
    }
    for task, expected_count in expected_counts.items():
        if expected_count is not None and counts[task] != expected_count:
            errors.append(f"{task}: expected {expected_count}, got {counts[task]}")

    report = {
        "validation_version": "training-messages-validation-v1",
        "status": "passed" if not errors else "failed",
        "files": len(message_files),
        "limit_per_file": args.limit_per_file,
        "counts": dict(sorted(counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "task_split_counts": dict(sorted(task_split_counts.items())),
        "unique_sample_task_pairs": len(pairs),
        "duplicate_sample_task_pairs": duplicate_pairs,
        "combined_content_sha256": digest.hexdigest(),
        "checks": {
            "message_schema": "passed" if not errors else "see errors",
            "deterministic_source_equivalence": "passed" if not errors else "see errors",
            "view_target_separation": "passed" if not errors else "see errors",
            "train_test_path_preservation": "passed" if not errors else "see errors",
        },
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
