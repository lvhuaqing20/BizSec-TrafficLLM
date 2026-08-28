from __future__ import annotations

from typing import Any, Mapping, Tuple

from .errors import ViewConstructionError


class RepresentationSelector:
    def __init__(self, policy: Mapping[str, Any]) -> None:
        if policy.get("selection_version") != "view-representation-selection-v1":
            raise ValueError("view-representation-selection-v1 is required")
        self._policy = policy

    def select(self, sample: Mapping[str, Any], task: str) -> Tuple[str, Mapping[str, Any], bool]:
        task_policy = self._policy.get("tasks", {}).get(task)
        if task_policy is None:
            raise ViewConstructionError("unknown_task", f"unsupported task: {task}")
        traffic = sample.get("traffic", {})
        representations = traffic.get("representations", {})
        if not isinstance(representations, dict):
            raise ViewConstructionError("invalid_canonical_sample", "traffic.representations is missing")
        allowed = set(task_policy["allowed"])
        primary = traffic.get("primary_representation")
        if self._policy.get("primary_if_allowed") and primary in allowed:
            value = representations.get(primary)
            if isinstance(value, dict):
                return primary, value, False
        for representation_type in task_policy["fallback_preference"]:
            value = representations.get(representation_type)
            if representation_type in allowed and isinstance(value, dict):
                return representation_type, value, True
        available = sorted(name for name, value in representations.items() if isinstance(value, dict))
        raise ViewConstructionError(
            "view_unavailable",
            f"task {task} has no allowed representation; available={available}",
        )
