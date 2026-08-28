from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional


class CanonicalValidator:
    """Runtime validator with optional Draft 2020-12 support and mandatory invariants."""

    def __init__(self, schema_root: Path) -> None:
        self.schema_root = schema_root
        self._official = self._build_official_validator()

    @property
    def validation_mode(self) -> str:
        return "draft-2020-12+semantic" if self._official is not None else "semantic-only"

    @staticmethod
    def _load(path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _build_official_validator(self) -> Optional[Any]:
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            return None
        schemas = [self._load(path) for path in sorted(self.schema_root.rglob("*.schema.json"))]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas if "$id" in schema
        )
        canonical = self._load(self.schema_root / "canonical" / "canonical_traffic_sample.schema.json")
        Draft202012Validator.check_schema(canonical)
        return Draft202012Validator(canonical, registry=registry)

    def issues(self, sample: Mapping[str, Any]) -> List[str]:
        issues: List[str] = []
        if self._official is not None:
            for error in sorted(self._official.iter_errors(sample), key=lambda item: list(item.path)):
                path = ".".join(str(token) for token in error.path) or "$"
                issues.append(f"schema:{path}:{error.message}")
        source = sample.get("source", {})
        source_file = source.get("source_file") if isinstance(source, dict) else None
        if isinstance(source_file, str):
            path = PurePosixPath(source_file)
            if path.is_absolute() or ".." in path.parts:
                issues.append("semantic:unsafe_source_path")
        traffic = sample.get("traffic", {})
        representations = traffic.get("representations", {}) if isinstance(traffic, dict) else {}
        if isinstance(representations, dict):
            actual = {name for name, value in representations.items() if value is not None}
            if traffic.get("primary_representation") not in actual:
                issues.append("semantic:primary_representation_missing")
            quality = sample.get("quality", {})
            available = set(quality.get("available_representations", [])) if isinstance(quality, dict) else set()
            if actual != available:
                issues.append("semantic:available_representations_mismatch")
        labels = sample.get("labels")
        if isinstance(labels, dict):
            targets = labels.get("targets", {})
            actual_tasks = {name for name, value in targets.items() if value is not None}
            if actual_tasks != set(labels.get("eligible_tasks", [])):
                issues.append("semantic:eligible_tasks_mismatch")
        return issues
