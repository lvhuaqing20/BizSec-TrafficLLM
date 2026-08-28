from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from .models import ParsedTraffic


class TrafficParser(ABC):
    parser_id: str
    representation_type: str

    @abstractmethod
    def parse(self, record: Mapping[str, Any]) -> ParsedTraffic:
        """Parse one decoded TrafficLLM JSONL record or raise ConversionError."""
