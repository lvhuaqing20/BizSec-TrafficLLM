#!/usr/bin/env python3
"""Validate every canonical JSONL record in a conversion output directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bizsec_trafficllm.data.canonical_validation import CanonicalValidator  # noqa: E402


MAC_PATTERN = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
IPV4_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")


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
    parser.add_argument("--schema-root", type=Path, default=PROJECT_ROOT / "schemas")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-records", type=int)
    args = parser.parse_args()

    validator = CanonicalValidator(args.schema_root)
    errors = []
    records = 0
    parse_statuses = Counter()
    representations = Counter()
    missing_fields = Counter()
    warnings = Counter()
    privacy_transforms = Counter()
    eligible_tasks = Counter()
    sample_ids = set()
    duplicate_sample_ids = 0
    files = sorted((args.input_dir / "canonical").rglob("*.jsonl"))
    digest = hashlib.sha256()
    for path in files:
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                digest.update(raw_line)
                try:
                    sample = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{line_number}: invalid JSON: {exc}")
                    continue
                records += 1
                sample_id = sample.get("sample_id")
                if sample_id in sample_ids:
                    duplicate_sample_ids += 1
                    errors.append(f"{path}:{line_number}: duplicate sample_id {sample_id}")
                else:
                    sample_ids.add(sample_id)
                issues = validator.issues(sample)
                if issues:
                    errors.append(f"{path}:{line_number}: {' | '.join(issues[:3])}")
                if "instruction" in set(walk_keys(sample)):
                    errors.append(f"{path}:{line_number}: raw instruction key leaked")
                traffic_context = json.dumps(
                    {"traffic": sample.get("traffic"), "context": sample.get("context")},
                    ensure_ascii=False,
                )
                if MAC_PATTERN.search(traffic_context):
                    errors.append(f"{path}:{line_number}: MAC address leaked into traffic/context")
                if IPV4_PATTERN.search(traffic_context):
                    errors.append(f"{path}:{line_number}: exact IPv4 address leaked into traffic/context")
                quality = sample.get("quality", {})
                parse_statuses[quality.get("parse_status", "<missing>")] += 1
                representations[sample.get("traffic", {}).get("primary_representation", "<missing>")] += 1
                missing_fields.update(quality.get("missing_fields", []))
                warnings.update(quality.get("warnings", []))
                privacy_transforms.update(quality.get("privacy", {}).get("transforms", []))
                labels = sample.get("labels")
                if isinstance(labels, dict):
                    eligible_tasks.update(labels.get("eligible_tasks", []))

    failure_records = 0
    failure_files = sorted((args.input_dir / "failures").rglob("*.jsonl"))
    for path in failure_files:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                failure_records += 1
                try:
                    failure = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{line_number}: invalid failure JSON: {exc}")
                    continue
                if "instruction" in set(walk_keys(failure)):
                    errors.append(f"{path}:{line_number}: instruction leaked into failure record")

    if args.expected_records is not None and records + failure_records != args.expected_records:
        errors.append(
            f"record conservation failed: success={records} failure={failure_records} "
            f"expected={args.expected_records}"
        )

    report = {
        "validation_version": "converted-canonical-jsonl-v1",
        "status": "passed" if not errors else "failed",
        "validation_mode": validator.validation_mode,
        "canonical_files": len(files),
        "failure_files": len(failure_files),
        "records_validated": records,
        "failure_records": failure_records,
        "expected_records": args.expected_records,
        "unique_sample_ids": len(sample_ids),
        "duplicate_sample_ids": duplicate_sample_ids,
        "combined_content_sha256": digest.hexdigest(),
        "parse_statuses": dict(sorted(parse_statuses.items())),
        "representations": dict(sorted(representations.items())),
        "missing_fields": dict(sorted(missing_fields.items())),
        "warnings": dict(sorted(warnings.items())),
        "privacy_transforms": dict(sorted(privacy_transforms.items())),
        "eligible_tasks": dict(sorted(eligible_tasks.items())),
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
