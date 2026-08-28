from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

from .errors import ConversionError
from .models import ParsedTraffic
from .parser_base import TrafficParser


class CsicHttpParser(TrafficParser):
    parser_id = "csic_http_json_v1"
    representation_type = "http_request"
    marker = "The given HTTP request is as follows:"

    def __init__(
        self,
        sensitive_names: Sequence[str],
        long_digit_min_length: int = 8,
        redaction_tokens: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._sensitive_names = {name.lower() for name in sensitive_names}
        self._long_digits = re.compile(rf"(?<!\d)\d{{{long_digit_min_length},}}(?!\d)")
        self._email = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
        tokens = dict(redaction_tokens or {})
        self._sensitive_token = tokens.get("sensitive_value", "<REDACTED>")
        self._email_token = tokens.get("email", "<EMAIL>")
        self._number_token = tokens.get("long_number", "<NUM>")

    def _extract_request(self, instruction: str) -> Dict[str, Any]:
        marker_index = instruction.rfind(self.marker)
        if marker_index < 0:
            raise ConversionError("http_marker_not_found", "CSIC HTTP marker is missing")
        payload = instruction[marker_index + len(self.marker):].lstrip()
        object_start = payload.find("{")
        if object_start < 0:
            raise ConversionError("http_json_not_found", "HTTP JSON object is missing")
        try:
            decoded, end = json.JSONDecoder().raw_decode(payload[object_start:])
        except json.JSONDecodeError as exc:
            raise ConversionError("invalid_http_json", str(exc)) from exc
        if payload[object_start + end:].strip():
            raise ConversionError("ambiguous_http_json", "unexpected content follows HTTP JSON object")
        if not isinstance(decoded, dict):
            raise ConversionError("invalid_http_json", "HTTP payload must be a JSON object")
        return decoded

    def _redact_value(self, value: str) -> str:
        value = self._email.sub(self._email_token, value)
        return self._long_digits.sub(self._number_token, value)

    def _redact_parameters(self, value: str) -> Tuple[str, bool]:
        if not value:
            return value, False
        try:
            pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=False)
        except ValueError:
            return self._redact_value(value), True
        if not pairs and "=" not in value:
            redacted = self._redact_value(value)
            return redacted, redacted != value
        changed = False
        normalized = []
        for key, item_value in pairs:
            if key.lower() in self._sensitive_names:
                new_value = self._sensitive_token
            else:
                new_value = self._redact_value(item_value)
            changed = changed or new_value != item_value
            normalized.append((key, new_value))
        return urlencode(normalized, doseq=True, safe="<>/:;()'\""), changed

    def parse(self, record: Mapping[str, Any]) -> ParsedTraffic:
        instruction = record.get("instruction")
        if not isinstance(instruction, str):
            raise ConversionError("missing_instruction", "instruction must be a string")
        request = self._extract_request(instruction)
        method = request.get("method")
        url = request.get("url")
        body = request.get("body")
        if not isinstance(method, str) or not method.strip():
            raise ConversionError("missing_http_method", "HTTP method is required")
        if not isinstance(url, str) or not url.strip():
            raise ConversionError("missing_http_url", "HTTP URL is required")
        if body is None:
            body = ""
        if not isinstance(body, str):
            raise ConversionError("invalid_http_body", "HTTP body must be a string or null")

        parsed_url = urlsplit(url)
        path = parsed_url.path or "/"
        query, query_changed = self._redact_parameters(parsed_url.query)
        redacted_body, body_changed = self._redact_parameters(body)
        missing_fields = []
        if parsed_url.hostname is None:
            missing_fields.append("traffic.representations.http_request.host")
        transforms = ["redact_sensitive_http_values"]
        warnings = []
        if not query_changed and not body_changed:
            warnings.append("http_privacy_scan_completed_no_sensitive_value_match")
        return ParsedTraffic(
            representation_type=self.representation_type,
            representation={
                "representation_type": self.representation_type,
                "method": method.strip().upper(),
                "host": parsed_url.hostname,
                "path": path,
                "query": query or None,
                "body": redacted_body or None,
            },
            missing_fields=missing_fields,
            warnings=warnings,
            privacy_transforms=transforms,
        )
