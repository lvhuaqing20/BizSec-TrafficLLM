#!/usr/bin/env python3
"""Validate phase-1 task contracts, registry, and audit invariants."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_registry(registry: dict[str, Any], audit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if registry.get("registry_version") != "label-registry-v1":
        fail(errors, "Unexpected registry version")
    if registry.get("task_contract_version") != "task-contract-v1":
        fail(errors, "Unexpected task contract version")

    registry_datasets = set(registry.get("datasets", {}))
    audit_datasets = set(audit.get("datasets", {}))
    if registry_datasets != audit_datasets:
        fail(errors, "Registry and audit dataset sets differ")

    computed_train = 0
    computed_test = 0
    declared_label_total = 0
    review_items = []

    for dataset, ds in registry["datasets"].items():
        labels = ds.get("labels", [])
        declared_label_total += len(labels)
        raw_labels = [item["raw_label"] for item in labels]
        original_ids = [item["original_id"] for item in labels]
        if len(raw_labels) != len(set(raw_labels)):
            fail(errors, f"{dataset}: duplicate raw labels")
        if len(original_ids) != len(set(original_ids)):
            fail(errors, f"{dataset}: duplicate original label ids")

        for item in labels:
            tasks = item["targets"]
            actual_eligible = sorted(name for name, target in tasks.items() if target is not None)
            if sorted(item["eligible_tasks"]) != actual_eligible:
                fail(errors, f"{dataset}/{item['raw_label']}: eligible task mismatch")

            detection = tasks["detection"]
            attack_type = tasks["attack_type"]
            if attack_type is not None:
                if detection is None or detection.get("is_attack") is not True:
                    fail(errors, f"{dataset}/{item['raw_label']}: attack type lacks positive detection target")
                if attack_type.get("attack_type") == "benign":
                    fail(errors, f"{dataset}/{item['raw_label']}: benign cannot train Attack-Type Adapter")
            if detection is not None and detection.get("is_attack") is False and attack_type is not None:
                fail(errors, f"{dataset}/{item['raw_label']}: benign detection has attack target")

            if item.get("review_required"):
                review_items.append(f"{dataset}/{item['raw_label']}")

        audit_ds = audit["datasets"][dataset]
        if audit_ds["declared_class_count"] != len(labels):
            fail(errors, f"{dataset}: declared class count mismatch")
        declared_set = set(raw_labels)
        for split in ("train", "test"):
            split_info = audit_ds["splits"][split]
            if split_info["malformed_count"] != 0:
                fail(errors, f"{dataset}/{split}: malformed JSON records found")
            observed_set = set(split_info["normalized_output_counts"])
            undeclared = observed_set - declared_set
            if undeclared:
                fail(errors, f"{dataset}/{split}: undeclared normalized outputs {sorted(undeclared)}")
            count_from_labels = sum(split_info["normalized_output_counts"].values())
            if count_from_labels != split_info["record_count"]:
                fail(errors, f"{dataset}/{split}: record count mismatch")
        computed_train += audit_ds["splits"]["train"]["record_count"]
        computed_test += audit_ds["splits"]["test"]["record_count"]

    totals = audit["totals"]
    if computed_train != totals["train_records"]:
        fail(errors, "Audit total train count mismatch")
    if computed_test != totals["test_records"]:
        fail(errors, "Audit total test count mismatch")
    if computed_train + computed_test != totals["all_records"]:
        fail(errors, "Audit total record count mismatch")
    if totals["dataset_variants"] != len(registry_datasets):
        fail(errors, "Audit dataset variant count mismatch")
    if declared_label_total != 290:
        fail(errors, f"Expected 290 declared labels, got {declared_label_total}")
    if review_items != ["iscx-botnet-2014/IRC"]:
        fail(errors, f"Unexpected review-required items: {review_items}")
    return errors


def validate_schemas(schema_root: Path) -> tuple[list[str], str]:
    errors: list[str] = []
    schema_paths = sorted((schema_root / "adapters").glob("*.schema.json"))
    schema_paths.extend(sorted((schema_root / "pipeline").glob("*.schema.json")))
    if len(schema_paths) != 4:
        fail(errors, f"Expected 3 Adapter schemas and 1 pipeline schema, found {len(schema_paths)}")
    schemas = {}
    for path in schema_paths:
        try:
            schemas[path.name] = load_json(path)
        except Exception as exc:  # noqa: BLE001
            fail(errors, f"{path.name}: invalid JSON: {exc}")

    expected_names = {
        "business_output.schema.json",
        "detection_output.schema.json",
        "attack_type_output.schema.json",
        "analysis_result.schema.json",
    }
    if set(schemas) != expected_names:
        fail(errors, f"Unexpected schema set: {sorted(schemas)}")
    forbidden_by_schema = {
        "business_output.schema.json": {"confidence", "evidence_codes", "decision_source"},
        "detection_output.schema.json": {"confidence", "evidence_codes", "decision_source"},
        "attack_type_output.schema.json": {"confidence", "evidence_codes", "decision_source"},
        "analysis_result.schema.json": {"confidence", "business_confidence", "attack_confidence"},
    }
    for name, forbidden in forbidden_by_schema.items():
        properties = set(schemas.get(name, {}).get("properties", {}))
        unexpected = properties & forbidden
        if unexpected:
            fail(errors, f"{name}: forbidden properties present: {sorted(unexpected)}")

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return errors, "jsonschema-not-installed; JSON syntax and custom invariants checked"

    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001
            fail(errors, f"{name}: invalid JSON Schema: {exc}")

    valid_examples = {
        "business_output.schema.json": {"business_domain": "application", "business_type": "spotify"},
        "detection_output.schema.json": {"is_attack": True},
        "attack_type_output.schema.json": {"attack_type": "malware", "attack_family": "Zeus"},
        "analysis_result.schema.json": {
            "request_id": "sample-1",
            "business_type": "spotify",
            "is_attack": False,
            "risk_score": 0.1,
            "attack_type": "benign",
            "risk_level": "low",
            "evidence": [],
            "recommended_action": [],
            "model_backend": "test-backend",
            "schema_version": "1.0",
        },
    }
    for name, example in valid_examples.items():
        try:
            Draft202012Validator(schemas[name]).validate(example)
        except Exception as exc:  # noqa: BLE001
            fail(errors, f"{name}: valid example rejected: {exc}")

    invalid_pipeline_example = dict(valid_examples["analysis_result.schema.json"])
    invalid_pipeline_example["attack_type"] = "malware"
    if "analysis_result.schema.json" in schemas:
        validator = Draft202012Validator(schemas["analysis_result.schema.json"])
        if validator.is_valid(invalid_pipeline_example):
            fail(errors, "Pipeline schema accepted malware while is_attack=false")

    forbidden_adapter_fields = {
        "business_output.schema.json": {
            "business_domain": "application",
            "business_type": "spotify",
            "confidence": 0.9,
        },
        "detection_output.schema.json": {"is_attack": True, "evidence_codes": []},
        "attack_type_output.schema.json": {
            "attack_type": "malware",
            "attack_family": "Zeus",
            "decision_source": "adapter",
        },
    }
    for name, example in forbidden_adapter_fields.items():
        if name in schemas and Draft202012Validator(schemas[name]).is_valid(example):
            fail(errors, f"{name}: accepted a field that belongs outside the Adapter output")
    return errors, "jsonschema Draft 2020-12 validation executed"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    registry = load_json(args.registry)
    audit = load_json(args.audit)
    errors = validate_registry(registry, audit)
    schema_errors, schema_status = validate_schemas(args.schema_root)
    errors.extend(schema_errors)

    report = {
        "validation_version": "phase1-contract-validation-v1",
        "status": "passed" if not errors else "failed",
        "checks": {
            "registry_and_audit_invariants": "passed" if not validate_registry(registry, audit) else "failed",
            "schema_validation": schema_status,
            "adapter_schemas": 3,
            "pipeline_schemas": 1,
            "dataset_variants": audit["totals"]["dataset_variants"],
            "records_checked": audit["totals"]["all_records"],
            "declared_labels_checked": sum(len(ds["labels"]) for ds in registry["datasets"].values()),
            "review_required_items": [
                f"{dataset}/{item['raw_label']}"
                for dataset, ds in registry["datasets"].items()
                for item in ds["labels"]
                if item["review_required"]
            ],
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
