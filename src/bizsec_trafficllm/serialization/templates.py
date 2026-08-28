from __future__ import annotations

from typing import Any, Dict, Mapping

from .errors import SerializationError


TASKS = ("business", "detection", "attack_type")


class PromptTemplates:
    def __init__(self, config: Mapping[str, Any]) -> None:
        if config.get("config_version") != "prompt-templates-v1":
            raise ValueError("prompt-templates-v1 is required")
        if config.get("format") != "chat_messages":
            raise ValueError("chat_messages format is required")
        if config.get("content_serialization") != "canonical-json-v1":
            raise ValueError("canonical-json-v1 content serialization is required")
        tasks = config.get("tasks")
        if not isinstance(tasks, dict) or set(tasks) != set(TASKS):
            raise ValueError("prompt config must define exactly the three tasks")
        self._tasks: Dict[str, Dict[str, str]] = {}
        for task in TASKS:
            profile = tasks[task]
            if not isinstance(profile, dict) or set(profile) != {"template_version", "system"}:
                raise ValueError(f"invalid prompt profile: {task}")
            if not all(isinstance(profile[key], str) and profile[key] for key in profile):
                raise ValueError(f"empty prompt profile value: {task}")
            self._tasks[task] = dict(profile)

    def profile(self, task: str) -> Dict[str, str]:
        try:
            return dict(self._tasks[task])
        except KeyError as exc:
            raise SerializationError("unknown_task", task) from exc
