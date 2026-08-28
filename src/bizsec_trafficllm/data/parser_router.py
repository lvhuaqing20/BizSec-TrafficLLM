from __future__ import annotations

from typing import Any, Dict, Mapping

from .csic_http_parser import CsicHttpParser
from .direction_sequence_parser import DirectionSequenceParser
from .errors import ConversionError
from .models import ParsedTraffic
from .parser_base import TrafficParser
from .tshark_packet_parser import TsharkPacketParser


class ParserRouter:
    def __init__(self, privacy_policy: Mapping[str, Any]) -> None:
        network = privacy_policy["network"]
        http = privacy_policy["http"]
        payload = privacy_policy["payload"]
        parsers = [
            TsharkPacketParser(network["internal_cidrs"], payload["max_content_characters"]),
            CsicHttpParser(
                http["sensitive_parameter_names_case_insensitive"],
                http["long_digit_min_length"],
                http["redaction_tokens"],
            ),
            DirectionSequenceParser(),
        ]
        self._parsers: Dict[str, TrafficParser] = {parser.parser_id: parser for parser in parsers}

    @property
    def parser_ids(self) -> set:
        return set(self._parsers)

    def parse(self, parser_id: str, expected_representation: str, record: Mapping[str, Any]) -> ParsedTraffic:
        parser = self._parsers.get(parser_id)
        if parser is None:
            raise ConversionError("unknown_parser", f"parser is not registered: {parser_id}")
        result = parser.parse(record)
        if result.representation_type != expected_representation:
            raise ConversionError(
                "representation_mismatch",
                f"expected {expected_representation}, parser returned {result.representation_type}",
            )
        return result
