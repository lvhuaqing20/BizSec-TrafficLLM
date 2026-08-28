from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .builder import ViewEngine
from .errors import ViewConstructionError


class TrainingViewGenerator:
    """Build label-separated task examples; targets are never inserted into views."""

    def __init__(self, engine: ViewEngine) -> None:
        self._engine = engine

    def build_example(self, sample: Mapping[str, Any], task: str) -> Dict[str, Any]:
        labels = sample.get("labels")
        if not isinstance(labels, dict):
            raise ViewConstructionError("training_labels_missing", "training sample labels are null")
        target = labels.get("targets", {}).get(task)
        if target is None:
            raise ViewConstructionError("training_target_unavailable", f"no target for task {task}")
        view = self._engine.build(sample, task, business_prior=None, security_context=None)
        return {
            "example_version": "task-training-example-v1",
            "sample_id": sample["sample_id"],
            "task": task,
            "view": view,
            "target": dict(target),
        }
