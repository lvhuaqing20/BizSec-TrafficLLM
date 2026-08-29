"""Reusable ChatGLM2 single-task inference components."""

from .interface import (
    ChatGLM2InferenceInterface,
    InferenceInterfaceError,
    request_to_query,
)

__all__ = [
    "ChatGLM2InferenceInterface",
    "InferenceInterfaceError",
    "request_to_query",
]
