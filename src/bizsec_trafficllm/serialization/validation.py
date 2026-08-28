from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import SerializationError


SCHEMA_NAMES = {
    "training": "training_message_example.schema.json",
    "inference": "inference_message_request.schema.json",
}
ADAPTER_SCHEMA_NAMES = {
    "business": "business_output.schema.json",
    "detection": "detection_output.schema.json",
    "attack_type": "attack_type_output.schema.json",
}
VIEW_SCHEMA_NAMES = {
    "business": "business_view.schema.json",
    "detection": "detection_view.schema.json",
    "attack_type": "attack_type_view.schema.json",
}


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class MessageValidator:
    def __init__(self, schema_root: Path) -> None:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource

        schemas = [_load_json(path) for path in schema_root.rglob("*.schema.json")]
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in schemas
            if "$id" in schema
        )
        by_name = {schema.get("$id", "").rsplit("/", 1)[-1]: schema for schema in schemas}
        self._message_validators = {
            mode: Draft202012Validator(by_name[name], registry=registry)
            for mode, name in SCHEMA_NAMES.items()
        }
        self._adapter_validators = {
            task: Draft202012Validator(by_name[name], registry=registry)
            for task, name in ADAPTER_SCHEMA_NAMES.items()
        }
        self._view_validators = {
            task: Draft202012Validator(by_name[name], registry=registry)
            for task, name in VIEW_SCHEMA_NAMES.items()
        }

    @staticmethod
    def _validate_with(validator: Any, value: Mapping[str, Any], code: str) -> None:
        errors = sorted(validator.iter_errors(value), key=lambda item: list(item.path))
        if errors:
            path = ".".join(str(item) for item in errors[0].path) or "<root>"
            raise SerializationError(code, f"{path}: {errors[0].message}")

    def validate_message(self, mode: str, value: Mapping[str, Any]) -> None:
        try:
            validator = self._message_validators[mode]
        except KeyError as exc:
            raise SerializationError("unknown_message_mode", mode) from exc
        self._validate_with(validator, value, "message_schema_invalid")

    def validate_target(self, task: str, target: Mapping[str, Any]) -> None:
        try:
            validator = self._adapter_validators[task]
        except KeyError as exc:
            raise SerializationError("unknown_task", task) from exc
        self._validate_with(validator, target, "target_schema_invalid")

    def validate_view(self, task: str, view: Mapping[str, Any]) -> None:
        try:
            validator = self._view_validators[task]
        except KeyError as exc:
            raise SerializationError("unknown_task", task) from exc
        self._validate_with(validator, view, "view_schema_invalid")
