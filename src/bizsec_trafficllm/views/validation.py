from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import ViewConstructionError


class ViewValidator:
    def __init__(self, schema_root: Path) -> None:
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError as exc:
            raise RuntimeError("jsonschema is required for View Engine runtime validation") from exc
        schemas = [json.loads(path.read_text(encoding="utf-8")) for path in schema_root.rglob("*.schema.json")]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas if "$id" in schema
        )
        names = {
            "business": "business_view.schema.json",
            "detection": "detection_view.schema.json",
            "attack_type": "attack_type_view.schema.json",
        }
        self._validators: Dict[str, Any] = {}
        for task, name in names.items():
            schema = next(item for item in schemas if item.get("$id", "").endswith("/" + name))
            Draft202012Validator.check_schema(schema)
            self._validators[task] = Draft202012Validator(schema, registry=registry)

    def validate(self, task: str, view: Mapping[str, Any]) -> None:
        errors = sorted(self._validators[task].iter_errors(view), key=lambda item: list(item.path))
        if errors:
            error = errors[0]
            path = ".".join(str(token) for token in error.path) or "$"
            raise ViewConstructionError("view_schema_invalid", f"{path}: {error.message}")
