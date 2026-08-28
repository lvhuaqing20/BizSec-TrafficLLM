from __future__ import annotations

from typing import Any, Dict, Mapping

from .canonical_json import dumps_canonical
from .errors import SerializationError
from .templates import PromptTemplates
from .validation import MessageValidator


class PromptSerializer:
    def __init__(
        self,
        schema_root: Any,
        prompt_config: Mapping[str, Any],
    ) -> None:
        self._templates = PromptTemplates(prompt_config)
        self._validator = MessageValidator(schema_root)

    def serialize_training(
        self,
        example: Mapping[str, Any],
        dataset_id: str,
        split: str,
    ) -> Dict[str, Any]:
        task = example.get("task")
        profile = self._templates.profile(task)
        view = example.get("view")
        target = example.get("target")
        if not isinstance(view, Mapping):
            raise SerializationError("invalid_view", "view must be an object")
        if not isinstance(target, Mapping):
            raise SerializationError("invalid_target", "target must be an object")
        if example.get("sample_id") != view.get("sample_id"):
            raise SerializationError("sample_id_mismatch", "example and view sample IDs differ")
        if split not in {"train", "test"}:
            raise SerializationError("invalid_split", split)
        if not isinstance(dataset_id, str) or not dataset_id:
            raise SerializationError("invalid_dataset_id", "dataset_id must be non-empty")
        self._validator.validate_view(task, view)
        self._validator.validate_target(task, target)
        result = {
            "message_version": "training-message-example-v1",
            "sample_id": example["sample_id"],
            "task": task,
            "messages": [
                {"role": "system", "content": profile["system"]},
                {"role": "user", "content": dumps_canonical(view)},
                {"role": "assistant", "content": dumps_canonical(target)},
            ],
            "metadata": {
                "dataset_id": dataset_id,
                "split": split,
                "template_version": profile["template_version"],
                "source_example_version": example.get("example_version"),
            },
        }
        self._validator.validate_message("training", result)
        return result

    def serialize_inference(self, view: Mapping[str, Any], task: str) -> Dict[str, Any]:
        profile = self._templates.profile(task)
        self._validator.validate_view(task, view)
        sample_id = view.get("sample_id")
        result = {
            "message_version": "inference-message-request-v1",
            "sample_id": sample_id,
            "task": task,
            "messages": [
                {"role": "system", "content": profile["system"]},
                {"role": "user", "content": dumps_canonical(view)},
            ],
            "metadata": {"template_version": profile["template_version"]},
        }
        self._validator.validate_message("inference", result)
        return result
