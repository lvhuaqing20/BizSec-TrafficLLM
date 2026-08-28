#!/usr/bin/env python3
"""Validate generated training examples and enforce view/target separation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


FORBIDDEN_VIEW_KEYS = {
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


def build_validator(schema_root: Path):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schemas = [load_json(path) for path in schema_root.rglob("*.schema.json")]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas if "$id" in schema
    )
    schema = next(item for item in schemas if item.get("$id", "").endswith("/task_example.schema.json"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-business", type=int)
    parser.add_argument("--expected-detection", type=int)
    parser.add_argument("--expected-attack-type", type=int)
    args = parser.parse_args()

    validator = build_validator(args.schema_root)
    counts = Counter()
    errors = []
    pairs = set()
    duplicate_pairs = 0
    digest = hashlib.sha256()
    files = sorted((args.input_dir / "examples").rglob("*.jsonl"))
    for path in files:
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                digest.update(raw_line)
                try:
                    example = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{line_number}: invalid JSON: {exc}")
                    continue
                official_errors = list(validator.iter_errors(example))
                if official_errors:
                    errors.append(f"{path}:{line_number}: {official_errors[0].message}")
                task = example.get("task", "<missing>")
                counts[task] += 1
                pair = (example.get("sample_id"), task)
                if pair in pairs:
                    duplicate_pairs += 1
                    errors.append(f"{path}:{line_number}: duplicate sample/task pair {pair}")
                pairs.add(pair)
                leaked = set(walk_keys(example.get("view"))) & FORBIDDEN_VIEW_KEYS
                if leaked:
                    errors.append(f"{path}:{line_number}: forbidden view keys {sorted(leaked)}")

    expected = {
        "business": args.expected_business,
        "detection": args.expected_detection,
        "attack_type": args.expected_attack_type,
    }
    for task, value in expected.items():
        if value is not None and counts[task] != value:
            errors.append(f"{task}: expected {value}, got {counts[task]}")
    report = {
        "validation_version": "task-training-views-validation-v1",
        "status": "passed" if not errors else "failed",
        "files": len(files),
        "counts": dict(sorted(counts.items())),
        "unique_sample_task_pairs": len(pairs),
        "duplicate_sample_task_pairs": duplicate_pairs,
        "combined_content_sha256": digest.hexdigest(),
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
