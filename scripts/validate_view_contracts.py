#!/usr/bin/env python3
"""Validate phase-2 View schemas, policies, and positive/negative fixtures."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


VIEW_TOP_LEVEL_KEYS = {
    "view_version",
    "task",
    "sample_id",
    "granularity",
    "traffic",
    "context",
    "priors",
    "quality",
}

VIEW_PROFILES = {
    "business_view.schema.json": {
        "view_version": "business-view-v1",
        "task": "business_classification",
        "allowed_representations": {"packet", "http_request", "direction_sequence"},
        "prior_keys": set(),
    },
    "detection_view.schema.json": {
        "view_version": "detection-view-v1",
        "task": "attack_detection",
        "allowed_representations": {"packet", "http_request"},
        "prior_keys": {"business"},
    },
    "attack_type_view.schema.json": {
        "view_version": "attack-type-view-v1",
        "task": "attack_type_classification",
        "allowed_representations": {"packet", "http_request"},
        "prior_keys": {"business"},
    },
}

REPRESENTATION_CONSISTENCY = {
    "packet": ("packet", "packet"),
    "http_request": ("request", "http_request"),
    "direction_sequence": ("direction_sequence", "direction_sequence"),
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


def validate_configs(config_root: Path) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    field_registry = load_json(config_root / "field_registry_v1.json")
    token_policy = load_json(config_root / "token_budget_v1.json")
    leakage_policy = load_json(config_root / "leakage_policy_v1.json")

    fields = field_registry.get("fields", [])
    field_ids = [item.get("field_id") for item in fields]
    if len(field_ids) != len(set(field_ids)):
        errors.append("field_registry: duplicate field_id")
    known_views = set(field_registry.get("allowed_views", []))
    known_priorities = set(field_registry.get("priority_levels", []))
    for item in fields:
        field_id = item.get("field_id", "<missing>")
        allowed = set(item.get("allowed_views", []))
        if not allowed <= known_views:
            errors.append(f"field_registry/{field_id}: unknown allowed view")
        if item.get("priority") not in known_priorities:
            errors.append(f"field_registry/{field_id}: unknown priority")
        if item.get("priority") == "FORBIDDEN" and allowed:
            errors.append(f"field_registry/{field_id}: forbidden field has allowed views")

    known_field_ids = set(field_ids)
    for view_name, overrides in token_policy.get("view_priority_overrides", {}).items():
        if view_name not in known_views:
            errors.append(f"token_budget: unknown view {view_name}")
        promote = set(overrides.get("promote", []))
        demote = set(overrides.get("demote", []))
        unknown = (promote | demote) - known_field_ids
        if unknown:
            errors.append(f"token_budget/{view_name}: unknown fields {sorted(unknown)}")
        overlap = promote & demote
        if overlap:
            errors.append(f"token_budget/{view_name}: fields both promoted and demoted {sorted(overlap)}")

    forbidden_keys = {key.lower() for key in leakage_policy.get("forbidden_keys_case_insensitive", [])}
    mandatory_forbidden = {
        "output",
        "raw_label",
        "ground_truth",
        "target",
        "candidate_labels",
        "instruction",
        "confidence",
        "evidence_codes",
        "decision_source",
    }
    missing_forbidden = mandatory_forbidden - forbidden_keys
    if missing_forbidden:
        errors.append(f"leakage_policy: missing mandatory forbidden keys {sorted(missing_forbidden)}")

    return errors, {
        "registered_fields": len(fields),
        "forbidden_fields": sum(item.get("priority") == "FORBIDDEN" for item in fields),
        "leakage_key_rules": len(forbidden_keys),
        "leakage_content_patterns": len(leakage_policy.get("forbidden_content_patterns_case_insensitive", [])),
    }


def validate_schema_references(schema_root: Path) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    schema_paths = sorted((schema_root / "views").glob("*.schema.json"))
    schemas = [load_json(path) for path in schema_paths]
    by_id = {schema.get("$id"): schema for schema in schemas if schema.get("$id")}
    expected_names = {
        "shared_definitions.schema.json",
        "business_view.schema.json",
        "detection_view.schema.json",
        "attack_type_view.schema.json",
    }
    if {path.name for path in schema_paths} != expected_names:
        errors.append("view schemas: unexpected schema file set")

    refs_checked = 0
    for schema in schemas:
        current_id = schema.get("$id")
        for key, value in walk(schema):
            if key != "$ref" or not isinstance(value, str):
                continue
            refs_checked += 1
            if value.startswith("#"):
                target_schema = schema
                fragment = value[1:]
            else:
                target_id, marker, fragment = value.partition("#")
                target_schema = by_id.get(target_id)
                if target_schema is None:
                    errors.append(f"{current_id}: unresolved schema id in $ref {value}")
                    continue
                fragment = fragment if marker else ""
            if not fragment:
                continue
            if not fragment.startswith("/"):
                errors.append(f"{current_id}: unsupported JSON Pointer in $ref {value}")
                continue
            target: Any = target_schema
            try:
                for token in fragment.lstrip("/").split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    target = target[token]
            except (KeyError, TypeError):
                errors.append(f"{current_id}: unresolved JSON Pointer in $ref {value}")

    return errors, {"view_schema_documents": len(schemas), "schema_references_checked": refs_checked}


def custom_view_issues(view: dict[str, Any], schema_name: str, leakage_policy: dict[str, Any]) -> set[str]:
    issues: set[str] = set()
    profile = VIEW_PROFILES[schema_name]

    if set(view) != VIEW_TOP_LEVEL_KEYS:
        issues.add("schema")
    if view.get("view_version") != profile["view_version"] or view.get("task") != profile["task"]:
        issues.add("schema")

    traffic = view.get("traffic")
    representation = traffic.get("representation") if isinstance(traffic, dict) else None
    representation_type = representation.get("representation_type") if isinstance(representation, dict) else None
    if representation_type not in profile["allowed_representations"]:
        issues.add("schema")

    if representation_type in REPRESENTATION_CONSISTENCY:
        expected_granularity, expected_source = REPRESENTATION_CONSISTENCY[representation_type]
        quality = view.get("quality", {})
        if view.get("granularity") != expected_granularity or quality.get("source_representation") != expected_source:
            issues.add("representation_consistency")
    else:
        issues.add("representation_consistency")

    if representation_type == "direction_sequence":
        sequence = representation.get("sequence", "")
        if not isinstance(sequence, str) or re.fullmatch(r"[01]+", sequence) is None:
            issues.add("schema")

    priors = view.get("priors")
    if not isinstance(priors, dict) or set(priors) != profile["prior_keys"]:
        issues.add("schema")
    elif "business" in priors and priors["business"] is not None:
        business = priors["business"]
        if not isinstance(business, dict) or set(business) != {"business_domain", "business_type"}:
            issues.add("schema")

    forbidden_keys = {key.lower() for key in leakage_policy["forbidden_keys_case_insensitive"]}
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in leakage_policy["forbidden_content_patterns_case_insensitive"]]
    for key, value in walk(view):
        if key is not None and key.lower() in forbidden_keys:
            issues.add("forbidden_key")
        if isinstance(value, str) and any(pattern.search(value) for pattern in patterns):
            issues.add("forbidden_content")

    return issues


def build_official_validators(schema_root: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None, "jsonschema-not-installed; custom structural and semantic validation executed"

    schemas = [load_json(path) for path in sorted(schema_root.rglob("*.schema.json"))]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas if "$id" in schema
    )
    validators: dict[str, Any] = {}
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
        if schema.get("$id", "").endswith("_view.schema.json"):
            name = schema["$id"].rsplit("/", 1)[-1]
            validators[name] = Draft202012Validator(schema, registry=registry)
    return validators, "jsonschema Draft 2020-12 validation executed"


def validate_fixtures(
    fixtures_dir: Path,
    schema_root: Path,
    leakage_policy: dict[str, Any],
) -> tuple[list[str], dict[str, Any], str]:
    errors: list[str] = []
    manifest = load_json(fixtures_dir / "manifest.json")
    validators, official_status = build_official_validators(schema_root)
    accepted_valid = 0
    rejected_invalid = 0

    for item in manifest["valid"]:
        view = load_json(fixtures_dir / item["file"])
        issues = custom_view_issues(view, item["schema"], leakage_policy)
        if validators is not None:
            official_errors = list(validators[item["schema"]].iter_errors(view))
            if official_errors:
                issues.add("schema")
        if issues:
            errors.append(f"valid fixture {item['file']} rejected by {sorted(issues)}")
        else:
            accepted_valid += 1

    for item in manifest["invalid"]:
        view = load_json(fixtures_dir / item["file"])
        issues = custom_view_issues(view, item["schema"], leakage_policy)
        if validators is not None:
            official_errors = list(validators[item["schema"]].iter_errors(view))
            if official_errors:
                issues.add("schema")
        expected = set(item["expected_rules"])
        missing = expected - issues
        if missing:
            errors.append(
                f"invalid fixture {item['file']} missed expected rules {sorted(missing)}; got {sorted(issues)}"
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
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    config_errors, config_stats = validate_configs(args.config_root)
    reference_errors, reference_stats = validate_schema_references(args.schema_root)
    leakage_policy = load_json(args.config_root / "leakage_policy_v1.json")
    fixture_errors, fixture_stats, official_status = validate_fixtures(
        args.fixtures_dir, args.schema_root, leakage_policy
    )
    errors = config_errors + reference_errors + fixture_errors
    report = {
        "validation_version": "phase2-view-validation-v1",
        "status": "passed" if not errors else "failed",
        "schema_validation": official_status,
        "checks": {
            "view_schemas": 3,
            "shared_definition_schemas": 1,
            **config_stats,
            **reference_stats,
            **fixture_stats,
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
