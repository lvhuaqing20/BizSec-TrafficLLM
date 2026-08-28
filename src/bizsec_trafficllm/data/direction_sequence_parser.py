from __future__ import annotations

import re
from typing import Any, Mapping

from .errors import ConversionError
from .models import ParsedTraffic
from .parser_base import TrafficParser


class DirectionSequenceParser(TrafficParser):
    parser_id = "binary_direction_sequence_v1"
    representation_type = "direction_sequence"
    _sequence_pattern = re.compile(r"Input\s*[:：]\s*(?:[:：]\s*)?([01]+)\s*$", re.IGNORECASE)

    def parse(self, record: Mapping[str, Any]) -> ParsedTraffic:
        instruction = record.get("instruction")
        if not isinstance(instruction, str):
            raise ConversionError("missing_instruction", "instruction must be a string")
        match = self._sequence_pattern.search(instruction)
        if match is None:
            raise ConversionError(
                "direction_sequence_not_found",
                "instruction does not end with a unique binary Input sequence",
            )
        sequence = match.group(1)
        return ParsedTraffic(
            representation_type=self.representation_type,
            representation={
                "representation_type": self.representation_type,
                "encoding": "binary",
                "sequence": sequence,
            },
        )
