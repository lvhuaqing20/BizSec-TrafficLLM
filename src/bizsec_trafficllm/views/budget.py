from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Tuple


class PreTokenBudgetManager:
    """Apply deterministic character limits before deployment tokenizer accounting."""

    def __init__(self, policy: Mapping[str, Any]) -> None:
        if policy.get("policy_version") != "view-token-budget-v1":
            raise ValueError("view-token-budget-v1 is required")
        self._limits = policy["pre_tokenization_limits"]

    @staticmethod
    def _truncate(value: Any, limit: int) -> Tuple[Any, bool]:
        if isinstance(value, str) and len(value) > limit:
            return value[:limit], True
        return value, False

    def apply(self, task: str, representation: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        result = copy.deepcopy(dict(representation))
        limits = self._limits[task]
        warnings: List[str] = []
        representation_type = result.get("representation_type")
        if representation_type == "packet":
            payload = result.get("payload")
            if isinstance(payload, dict):
                content, changed = self._truncate(
                    payload.get("content"), limits["payload_content_max_chars"]
                )
                payload["content"] = content
                if changed:
                    warnings.append("view_payload_content_truncated")
        elif representation_type == "http_request":
            for field, limit_key, warning in (
                ("query", "http_query_max_chars", "view_http_query_truncated"),
                ("body", "http_body_max_chars", "view_http_body_truncated"),
            ):
                value, changed = self._truncate(result.get(field), limits[limit_key])
                result[field] = value
                if changed:
                    warnings.append(warning)
        elif representation_type == "direction_sequence":
            sequence, changed = self._truncate(
                result.get("sequence"), limits["direction_sequence_max_chars"]
            )
            result["sequence"] = sequence
            if changed:
                warnings.append("view_direction_sequence_tail_truncated")
        return result, warnings
