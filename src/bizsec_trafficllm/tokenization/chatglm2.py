"""Adapt model-independent Messages records to ChatGLM2 P-Tuning v2 features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence


EXPECTED_ROLES = ("system", "user", "assistant")
SUPPORTED_TASKS = {"business", "detection", "attack_type"}


class MessageFormatError(ValueError):
    """Raised when a Messages record cannot be used for supervised training."""


@dataclass(frozen=True)
class TrainingTextPair:
    sample_id: str
    task: str
    query: str
    response: str


def message_to_text_pair(record: Mapping[str, Any]) -> TrainingTextPair:
    """Convert one three-role Messages record into ChatGLM2 query/response text.

    ChatGLM2 has no separate system-role input in the original paper code. The
    system instruction is therefore embedded in the query using one fixed,
    versioned separator. The assistant content remains the only loss target.
    """

    sample_id = record.get("sample_id")
    task = record.get("task")
    messages = record.get("messages")
    if not isinstance(sample_id, str) or not sample_id:
        raise MessageFormatError("sample_id must be a non-empty string")
    if task not in SUPPORTED_TASKS:
        raise MessageFormatError(f"unsupported task: {task!r}")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise MessageFormatError("messages must be a sequence")
    if len(messages) != 3:
        raise MessageFormatError("training messages must contain exactly three roles")

    roles = []
    contents = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise MessageFormatError(f"messages[{index}] must be an object")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str) or not content:
            raise MessageFormatError(f"messages[{index}] must have non-empty role/content")
        roles.append(role)
        contents.append(content)
    if tuple(roles) != EXPECTED_ROLES:
        raise MessageFormatError(
            f"expected roles {EXPECTED_ROLES}, got {tuple(roles)}"
        )

    system, user, assistant = contents
    query = f"{system}\n\nTraffic view:\n{user}"
    return TrainingTextPair(
        sample_id=sample_id,
        task=task,
        query=query,
        response=assistant,
    )


class ChatGLM2FeatureAdapter:
    """Create fixed-length, assistant-only-loss ChatGLM2 training features."""

    def __init__(
        self,
        tokenizer: Any,
        max_source_length: int,
        max_target_length: int,
    ) -> None:
        if max_source_length <= 0 or max_target_length <= 0:
            raise ValueError("token limits must be positive")
        if tokenizer.eos_token_id is None or tokenizer.pad_token_id is None:
            raise ValueError("tokenizer must define eos_token_id and pad_token_id")
        if not callable(getattr(tokenizer, "build_prompt", None)):
            raise ValueError("ChatGLM2 tokenizer must provide build_prompt")
        self.tokenizer = tokenizer
        self.max_source_length = max_source_length
        self.max_target_length = max_target_length

    def prompt_text(self, pair: TrainingTextPair) -> str:
        return self.tokenizer.build_prompt(pair.query, history=None)

    def raw_lengths(self, record: Mapping[str, Any]) -> Dict[str, int]:
        pair = message_to_text_pair(record)
        prompt = self.prompt_text(pair)
        source_ids = self.tokenizer.encode(prompt, add_special_tokens=True)
        target_ids = self.tokenizer.encode(pair.response, add_special_tokens=False)
        return {
            "source_tokens": len(source_ids),
            "target_tokens": len(target_ids),
            "total_tokens_with_eos": len(source_ids) + len(target_ids) + 1,
        }

    def encode(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        pair = message_to_text_pair(record)
        prompt = self.prompt_text(pair)
        raw_source_ids = self.tokenizer.encode(prompt, add_special_tokens=True)
        raw_target_ids = self.tokenizer.encode(pair.response, add_special_tokens=False)
        source_ids = raw_source_ids[: self.max_source_length]
        target_ids = raw_target_ids[: self.max_target_length]

        eos_token_id = self.tokenizer.eos_token_id
        pad_token_id = self.tokenizer.pad_token_id
        input_ids = source_ids + target_ids + [eos_token_id]
        labels = [-100] * len(source_ids) + target_ids + [eos_token_id]
        max_sequence_length = self.max_source_length + self.max_target_length + 1
        padding_length = max_sequence_length - len(input_ids)
        input_ids.extend([pad_token_id] * padding_length)
        labels.extend([-100] * padding_length)
        attention_mask = [1] * (max_sequence_length - padding_length) + [0] * padding_length

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
            "sample_id": pair.sample_id,
            "task": pair.task,
            "source_tokens": len(source_ids),
            "target_tokens": len(target_ids),
            "source_truncated": len(raw_source_ids) > self.max_source_length,
            "target_truncated": len(raw_target_ids) > self.max_target_length,
        }
