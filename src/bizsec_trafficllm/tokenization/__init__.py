"""ChatGLM2 message adaptation, feature construction, and token auditing."""

from .audit import audit_messages
from .chatglm2 import (
    ChatGLM2FeatureAdapter,
    MessageFormatError,
    TrainingTextPair,
    message_to_text_pair,
)

__all__ = [
    "ChatGLM2FeatureAdapter",
    "MessageFormatError",
    "TrainingTextPair",
    "audit_messages",
    "message_to_text_pair",
]
