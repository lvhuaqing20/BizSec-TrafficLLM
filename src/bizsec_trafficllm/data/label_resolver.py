from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from .errors import ConversionError


class LabelResolver:
    """Resolve raw dataset outputs through the frozen phase-1 label registry."""

    def __init__(self, registry: Mapping[str, Any]) -> None:
        if registry.get("registry_version") != "label-registry-v1":
            raise ValueError("label-registry-v1 is required")
        self._registry = registry
        self._indexes: Dict[str, Dict[str, Mapping[str, Any]]] = {}
        for dataset_id, dataset in registry["datasets"].items():
            self._indexes[dataset_id] = {
                item["normalized_label"]: item for item in dataset["labels"]
            }

    def normalize(self, dataset_id: str, raw_value: str) -> str:
        dataset = self._registry["datasets"].get(dataset_id)
        if dataset is None:
            raise ConversionError("unknown_dataset", f"dataset is absent from label registry: {dataset_id}")
        rules = dataset.get("output_normalization", {})
        value = raw_value.strip() if rules.get("strip_whitespace") else raw_value
        for suffix in rules.get("strip_suffixes", []):
            if value.endswith(suffix):
                value = value[: -len(suffix)].strip()
        value = rules.get("aliases", {}).get(value, value)
        return value

    def declared_labels(self, dataset_id: str) -> set:
        if dataset_id not in self._indexes:
            raise ConversionError("unknown_dataset", f"dataset is absent from label registry: {dataset_id}")
        return set(self._indexes[dataset_id])

    def resolve(self, dataset_id: str, raw_value: Any) -> Dict[str, Any]:
        if not isinstance(raw_value, str):
            raise ConversionError("invalid_output", "output must be a string")
        normalized = self.normalize(dataset_id, raw_value)
        label = self._indexes.get(dataset_id, {}).get(normalized)
        if label is None:
            raise ConversionError(
                "unknown_label",
                f"normalized label is absent from registry: {dataset_id}/{normalized}",
            )
        return {
            "raw": {"value": raw_value, "normalized_value": normalized},
            "eligible_tasks": list(label["eligible_tasks"]),
            "targets": copy.deepcopy(label["targets"]),
            "mapping": {
                "registry_version": "label-registry-v1",
                "mapping_basis": label["mapping_basis"],
                "review_required": bool(label["review_required"]),
            },
        }
