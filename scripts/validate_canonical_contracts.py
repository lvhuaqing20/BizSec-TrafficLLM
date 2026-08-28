#!/usr/bin/env python3
"""Validate phase-3 canonical sample schema, mappings, and fixtures."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CANONICAL_TOP_LEVEL_KEYS = {
    "canonical_version",
    "sample_id",
    "source",
    "traffic",
    "context",
    "labels",
    "quality",
}
REPRESENTATION_REF_NAMES = {
    "packetRepresentation": "packet",
    "httpRepresentation": "http_request",
    "directionSequenceRepresentation": "direction_sequence",
}
TASK_TO_VIEW_SCHEMA = {
    "business": "business_view.schema.json",
    "detection": "detection_view.schema.json",
    "attack_type": "attack_type_view.schema.json",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def walk(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from walk(child)


def infer_view_permissions(schema_root: Path) -> dict[str, set[str]]:
    permissions: dict[str, set[str]] = {}
    for task, schema_name in TASK_TO_VIEW_SCHEMA.items():
        schema = load_json(schema_root / "views" / schema_name)
        refs = schema["properties"]["traffic"]["properties"]["representation"]["oneOf"]
        allowed: set[str] = set()
        for item in refs:
            ref = item.get("$ref", "")
            definition_name = ref.rsplit("/", 1)[-1]
            if definition_name in REPRESENTATION_REF_NAMES:
                allowed.add(REPRESENTATION_REF_NAMES[definition_name])
        permissions[task] = allowed
    return permissions


def validate_configs(
    config_root: Path,
    schema_root: Path,
    registry: dict[str, Any],
    audit: dict[str, Any],
) -> tuple[list[str], dict[str, Any], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    source_mapping = load_json(config_root / "source_mapping_v1.json")
    detection = load_json(config_root / "representation_detection_v1.json")

    if source_mapping.get("mapping_version") != "canonical-source-mapping-v1":
        errors.append("source_mapping: unexpected mapping version")
    if detection.get("detection_version") != "representation-detection-v1":
        errors.append("representation_detection: unexpected version")

    components = source_mapping.get("sample_id", {}).get("components")
    if components != ["dataset_id", "split", "source_file", "record_index"]:
        errors.append("source_mapping: sample_id components changed")
    sample_id_config = source_mapping.get("sample_id", {})
    if sample_id_config.get("separator") != "NUL" or sample_id_config.get("separator_byte_hex") != "00":
        errors.append("source_mapping: sample_id separator must be the NUL byte")

    datasets = source_mapping.get("datasets", [])
    dataset_ids = [item.get("dataset_id") for item in datasets]
    if len(dataset_ids) != len(set(dataset_ids)):
        errors.append("source_mapping: duplicate dataset_id")
    expected_dataset_ids = set(registry.get("datasets", {}))
    if set(dataset_ids) != expected_dataset_ids:
        errors.append("source_mapping: dataset set differs from label registry")

    mapping_by_dataset = {item["dataset_id"]: item for item in datasets if item.get("dataset_id")}
    for dataset_id, item in mapping_by_dataset.items():
        audit_format = audit["datasets"][dataset_id]["input_format"]
        if item.get("source_format") != audit_format:
            errors.append(f"source_mapping/{dataset_id}: source_format differs from phase1 audit")
        expected_file_stem = Path(audit["datasets"][dataset_id]["splits"]["train"]["path"]).name
        expected_file_stem = expected_file_stem.removesuffix("_train.json")
        if item.get("file_stem") != expected_file_stem:
            errors.append(f"source_mapping/{dataset_id}: file_stem differs from phase1 audit")

    detectors = detection.get("detectors", [])
    detected_representations = [item.get("representation_type") for item in detectors]
    expected_representations = {"packet", "http_request", "direction_sequence"}
    if set(detected_representations) != expected_representations:
        errors.append("representation_detection: detector set is incomplete")
    if len(detected_representations) != len(set(detected_representations)):
        errors.append("representation_detection: duplicate representation detector")

    policy = detection.get("policy", {})
    required_policy = {
        "inspect_actual_content": True,
        "dataset_config_is_expectation_only": True,
        "on_declared_content_mismatch": "conversion_error",
        "on_ambiguous_match": "conversion_error",
        "allow_forced_conversion": False,
        "failed_records_are_canonical_samples": False,
    }
    if policy != required_policy:
        errors.append("representation_detection: safety policy changed")

    configured_permissions = {
        task: set(values) for task, values in detection.get("task_view_permissions", {}).items()
    }
    actual_permissions = infer_view_permissions(schema_root)
    if configured_permissions != actual_permissions:
        errors.append("representation_detection: task permissions differ from phase2 View schemas")

    return errors, {
        "dataset_mappings": len(datasets),
        "representation_detectors": len(detectors),
        "task_permission_profiles": len(configured_permissions),
    }, mapping_by_dataset


def validate_schema_references(schema_root: Path) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    schema_paths = sorted(schema_root.rglob("*.schema.json"))
    schemas = [load_json(path) for path in schema_paths]
    by_id = {schema.get("$id"): schema for schema in schemas if schema.get("$id")}
    canonical_path = schema_root / "canonical" / "canonical_traffic_sample.schema.json"
    if not canonical_path.exists():
        return ["canonical schema is missing"], {"canonical_schemas": 0, "schema_references_checked": 0}

    refs_checked = 0
    canonical_schema = load_json(canonical_path)
    for key, value in walk(canonical_schema):
        if key != "$ref" or not isinstance(value, str):
            continue
        refs_checked += 1
        if value.startswith("#"):
            target_schema = canonical_schema
            fragment = value[1:]
        else:
            target_id, marker, fragment = value.partition("#")
            target_schema = by_id.get(target_id)
            if target_schema is None:
                errors.append(f"canonical schema: unresolved schema id in $ref {value}")
                continue
            fragment = fragment if marker else ""
        if not fragment:
            continue
        if not fragment.startswith("/"):
            errors.append(f"canonical schema: unsupported JSON Pointer in $ref {value}")
            continue
        target: Any = target_schema
        try:
            for token in fragment.lstrip("/").split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                target = target[token]
        except (KeyError, TypeError):
            errors.append(f"canonical schema: unresolved JSON Pointer in $ref {value}")
    return errors, {"canonical_schemas": 1, "schema_references_checked": refs_checked}


def expected_dataset_sample_id(source: dict[str, Any]) -> str:
    components = [
        source["dataset_id"],
        source["split"],
        source["source_file"],
        str(source["record_index"]),
    ]
    return hashlib.sha256("\0".join(components).encode("utf-8")).hexdigest()


def custom_canonical_issues(
    sample: dict[str, Any],
    mapping_by_dataset: dict[str, dict[str, Any]],
) -> set[str]:
    issues: set[str] = set()
    if set(sample) != CANONICAL_TOP_LEVEL_KEYS:
        issues.add("schema")

    source = sample.get("source")
    if not isinstance(source, dict):
        return {"schema", "source_provenance"}
    source_kind = source.get("source_kind")
    if source_kind == "dataset":
        required_values = ("dataset_id", "source_file", "record_index", "source_record_sha256")
        if any(source.get(key) is None for key in required_values):
            issues.add("source_provenance")
        else:
            dataset_id = source["dataset_id"]
            mapping = mapping_by_dataset.get(dataset_id)
            if mapping is None or source.get("source_format") != mapping.get("source_format"):
                issues.add("source_provenance")
            source_path = source["source_file"]
            pure_path = PurePosixPath(source_path)
            if pure_path.is_absolute() or ".." in pure_path.parts:
                issues.add("source_provenance")
            if sample.get("sample_id") != expected_dataset_sample_id(source):
                issues.add("sample_id")
    elif source_kind == "live":
        nullable_fields = ("dataset_id", "source_file", "record_index", "source_record_sha256")
        if any(source.get(key) is not None for key in nullable_fields):
            issues.add("source_provenance")
        if source.get("split") != "inference" or source.get("source_format") != "live_structured":
            issues.add("source_provenance")
    else:
        issues.add("source_provenance")

    traffic = sample.get("traffic")
    representations = traffic.get("representations") if isinstance(traffic, dict) else None
    if not isinstance(representations, dict):
        issues.update({"schema", "representation_consistency"})
    else:
        actual = {name for name, value in representations.items() if value is not None}
        if not actual:
            issues.add("representation_consistency")
        primary = traffic.get("primary_representation")
        if primary not in actual:
            issues.add("representation_consistency")
        available = set(sample.get("quality", {}).get("available_representations", []))
        if available != actual:
            issues.add("representation_consistency")
        for name, value in representations.items():
            if isinstance(value, dict) and value.get("representation_type") != name:
                issues.add("representation_consistency")
        if source_kind == "dataset" and source.get("dataset_id") in mapping_by_dataset:
            expected_primary = mapping_by_dataset[source["dataset_id"]]["expected_representation"]
            if primary != expected_primary:
                issues.add("representation_consistency")

    labels = sample.get("labels")
    if source_kind == "dataset" and source.get("split") in {"train", "validation", "test"} and labels is None:
        issues.add("label_consistency")
    if isinstance(labels, dict):
        targets = labels.get("targets", {})
        actual_tasks = {name for name, target in targets.items() if target is not None}
        if set(labels.get("eligible_tasks", [])) != actual_tasks:
            issues.add("label_consistency")
        detection_target = targets.get("detection")
        attack_target = targets.get("attack_type")
        if attack_target is not None:
            if not isinstance(detection_target, dict) or detection_target.get("is_attack") is not True:
                issues.add("label_consistency")
        if isinstance(detection_target, dict) and detection_target.get("is_attack") is False and attack_target is not None:
            issues.add("label_consistency")

    quality = sample.get("quality", {})
    privacy = quality.get("privacy", {}) if isinstance(quality, dict) else {}
    if privacy.get("status") == "applied" and not privacy.get("transforms"):
        issues.add("privacy_consistency")
    if privacy.get("status") == "not_required" and privacy.get("transforms"):
        issues.add("privacy_consistency")
    if privacy.get("contains_direct_identifiers") is not False:
        issues.add("privacy_consistency")
    return issues


def build_official_validator(schema_root: Path) -> tuple[Any | None, str]:
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None, "jsonschema-not-installed; custom structural and semantic validation executed"

    schemas = [load_json(path) for path in sorted(schema_root.rglob("*.schema.json"))]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas if "$id" in schema
    )
    canonical = load_json(schema_root / "canonical" / "canonical_traffic_sample.schema.json")
    Draft202012Validator.check_schema(canonical)
    return Draft202012Validator(canonical, registry=registry), "jsonschema Draft 2020-12 validation executed"


def set_path(value: dict[str, Any], dotted_path: str, replacement: Any) -> None:
    tokens = dotted_path.split(".")
    target: Any = value
    for token in tokens[:-1]:
        target = target[token]
    target[tokens[-1]] = replacement


def materialize_invalid_fixture(fixtures_dir: Path, spec_path: Path) -> dict[str, Any]:
    spec = load_json(spec_path)
    sample = copy.deepcopy(load_json(fixtures_dir / spec["base_fixture"]))
    mutation = spec["mutation"]
    set_path(sample, mutation["path"], mutation["value"])
    return sample


def validate_fixtures(
    fixtures_dir: Path,
    schema_root: Path,
    mapping_by_dataset: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, Any], str]:
    errors: list[str] = []
    manifest = load_json(fixtures_dir / "manifest.json")
    validator, official_status = build_official_validator(schema_root)
    accepted_valid = 0
    rejected_invalid = 0

    for item in manifest["valid"]:
        sample = load_json(fixtures_dir / item["file"])
        issues = custom_canonical_issues(sample, mapping_by_dataset)
        if validator is not None and list(validator.iter_errors(sample)):
            issues.add("schema")
        if issues:
            errors.append(f"valid fixture {item['file']} rejected by {sorted(issues)}")
        else:
            accepted_valid += 1

    for item in manifest["invalid"]:
        sample = materialize_invalid_fixture(fixtures_dir, fixtures_dir / item["file"])
        issues = custom_canonical_issues(sample, mapping_by_dataset)
        if validator is not None and list(validator.iter_errors(sample)):
            issues.add("schema")
        expected = item["expected_issue"]
        if expected not in issues:
            errors.append(
                f"invalid fixture {item['file']} missed expected issue {expected}; got {sorted(issues)}"
            )
        elif not issues:
            errors.append(f"invalid fixture {item['file']} was accepted")
        else:
            rejected_invalid += 1

    return errors, {
        "valid_fixtures": len(manifest["valid"]),
        "valid_fixtures_accepted": accepted_valid,
        "invalid_fixtures": len(manifest["invalid"]),
        "invalid_fixtures_rejected": rejected_invalid,
    }, official_status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-root", type=Path, required=True)
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--fixtures-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    registry = load_json(args.registry)
    audit = load_json(args.audit)
    config_errors, config_stats, mapping_by_dataset = validate_configs(
        args.config_root, args.schema_root, registry, audit
    )
    reference_errors, reference_stats = validate_schema_references(args.schema_root)
    fixture_errors, fixture_stats, official_status = validate_fixtures(
        args.fixtures_dir, args.schema_root, mapping_by_dataset
    )
    errors = config_errors + reference_errors + fixture_errors
    report = {
        "validation_version": "phase3-canonical-validation-v1",
        "status": "passed" if not errors else "failed",
        "schema_validation": official_status,
        "checks": {
            **config_stats,
            **reference_stats,
            **fixture_stats,
            "label_registry_datasets": len(registry.get("datasets", {})),
            "audited_source_records": audit.get("totals", {}).get("all_records", 0)
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
