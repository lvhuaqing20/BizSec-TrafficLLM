from __future__ import annotations

import hashlib
import ipaddress
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import ConversionError
from .models import ParsedTraffic
from .parser_base import TrafficParser


class TsharkPacketParser(TrafficParser):
    parser_id = "tshark_packet_text_v1"
    representation_type = "packet"
    _field_boundary = re.compile(r"(?:^|, )(?P<key>[A-Za-z][A-Za-z0-9_.-]*): ")
    _hex = re.compile(r"^[0-9a-fA-F:]+$")
    _flag_fields = {
        "tcp.flags.fin": "FIN",
        "tcp.flags.syn": "SYN",
        "tcp.flags.reset": "RST",
        "tcp.flags.push": "PSH",
        "tcp.flags.ack": "ACK",
        "tcp.flags.urg": "URG",
        "tcp.flags.ecn": "ECE",
        "tcp.flags.cwr": "CWR",
    }

    def __init__(self, internal_cidrs: Sequence[str], max_payload_characters: int = 512) -> None:
        self._internal_networks = [ipaddress.ip_network(value) for value in internal_cidrs]
        self._max_payload_characters = max_payload_characters

    @staticmethod
    def _traffic_text(instruction: str) -> str:
        marker_index = instruction.rfind("<packet>:")
        if marker_index >= 0:
            return instruction[marker_index + len("<packet>:"):].strip()
        frame_match = re.search(r"(?:^|\n)(frame\.[A-Za-z0-9_.-]+: )", instruction)
        if frame_match is not None:
            return instruction[frame_match.start(1):].strip()
        raise ConversionError("packet_boundary_not_found", "packet body marker or first frame field is missing")

    def _parse_fields(self, text: str) -> Dict[str, List[str]]:
        matches = list(self._field_boundary.finditer(text))
        if not matches:
            raise ConversionError("packet_fields_not_found", "no TShark key/value fields found")
        fields: Dict[str, List[str]] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            value = text[match.end():end].strip()
            fields.setdefault(match.group("key"), []).append(value)
        prefixes = {key.split(".", 1)[0] for key in fields}
        if "frame" not in prefixes or len(prefixes & {"frame", "ip", "ipv6", "tcp", "udp", "tls", "icmp", "icmpv6"}) < 2:
            raise ConversionError("packet_signature_mismatch", "content does not satisfy TShark packet markers")
        return fields

    @staticmethod
    def _first(fields: Mapping[str, List[str]], *names: str) -> Optional[str]:
        for name in names:
            for value in fields.get(name, []):
                if value != "":
                    return value
        return None

    @staticmethod
    def _integer(value: Optional[str], field_name: str, required: bool = False) -> Optional[int]:
        if value is None:
            if required:
                raise ConversionError("missing_packet_field", f"{field_name} is required")
            return None
        try:
            return int(value.rsplit(",", 1)[-1].strip(), 0)
        except ValueError as exc:
            raise ConversionError("invalid_packet_integer", f"{field_name}={value!r}") from exc

    def _role(self, value: Optional[str]) -> str:
        if not value:
            return "unknown"
        try:
            address = ipaddress.ip_address(value.rsplit(",", 1)[-1].strip())
        except ValueError:
            return "unknown"
        return "internal" if any(address in network for network in self._internal_networks) else "external"

    @staticmethod
    def _direction(src_role: str, dst_role: str) -> str:
        if src_role == "internal" and dst_role == "external":
            return "outbound"
        if src_role == "external" and dst_role == "internal":
            return "inbound"
        if src_role == "internal" and dst_role == "internal":
            return "lateral"
        return "unknown"

    def _payload(self, fields: Mapping[str, List[str]], warnings: List[str], transforms: List[str]) -> Any:
        raw = self._first(fields, "tcp.payload", "udp.payload", "data.data")
        if raw is None:
            return None
        compact = raw.replace(":", "").replace(" ", "")
        if compact and self._hex.fullmatch(raw) and len(compact) % 2 == 0:
            content = compact.lower()
            byte_length = len(content) // 2
            if len(content) > self._max_payload_characters:
                content = content[:self._max_payload_characters]
                warnings.append("payload_content_truncated")
                transforms.append("truncate_payload_hex")
            return {"length": byte_length, "encoding": "hex", "content": content}
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        warnings.append("non_hex_payload_replaced_by_summary_hash")
        transforms.append("summarize_non_hex_payload")
        return {"length": len(raw.encode("utf-8")), "encoding": "summary", "content": f"sha256:{digest}"}

    def parse(self, record: Mapping[str, Any]) -> ParsedTraffic:
        instruction = record.get("instruction")
        if not isinstance(instruction, str):
            raise ConversionError("missing_instruction", "instruction must be a string")
        fields = self._parse_fields(self._traffic_text(instruction))
        warnings: List[str] = []
        transforms = ["ip_to_role", "drop_mac", "drop_absolute_time"]

        protocols_value = self._first(fields, "frame.protocols")
        protocols = []
        if protocols_value:
            protocols = list(dict.fromkeys(part for part in protocols_value.split(":") if part))
        if not protocols:
            protocols = list(
                key for key in ("ip", "ipv6", "tcp", "udp", "icmp", "icmpv6", "tls") if any(
                    field == key or field.startswith(key + ".") for field in fields
                )
            )
        if not protocols:
            raise ConversionError("missing_packet_protocols", "no packet protocols could be derived")

        packet_length = self._integer(self._first(fields, "frame.len", "ip.len", "ipv6.plen"), "frame.len", True)
        src_ip = self._first(fields, "ip.src", "ipv6.src")
        dst_ip = self._first(fields, "ip.dst", "ipv6.dst")
        if any(value is not None and "," in value for value in (src_ip, dst_ip, self._first(fields, "ip.version"))):
            warnings.append("multi_layer_ip_uses_innermost")
        src_role = self._role(src_ip)
        dst_role = self._role(dst_ip)
        ip_version_value = self._integer(self._first(fields, "ip.version", "ipv6.version"), "ip.version")
        if ip_version_value not in (4, 6, None):
            ip_version_value = None
            warnings.append("unsupported_ip_version_normalized_to_null")

        if any(key.startswith("tcp.") for key in fields):
            transport_protocol = "tcp"
            src_port = self._integer(self._first(fields, "tcp.srcport"), "tcp.srcport")
            dst_port = self._integer(self._first(fields, "tcp.dstport"), "tcp.dstport")
        elif any(key.startswith("udp.") for key in fields):
            transport_protocol = "udp"
            src_port = self._integer(self._first(fields, "udp.srcport"), "udp.srcport")
            dst_port = self._integer(self._first(fields, "udp.dstport"), "udp.dstport")
        elif any(key.startswith(("icmp.", "icmpv6.")) for key in fields):
            transport_protocol = "icmp"
            src_port = None
            dst_port = None
        else:
            transport_protocol = "other"
            src_port = None
            dst_port = None
        tcp_flags = [
            flag
            for field, flag in self._flag_fields.items()
            if (self._first(fields, field) or "").rsplit(",", 1)[-1].strip() == "1"
        ]

        has_tls = "tls" in protocols or any(key.startswith("tls.") for key in fields)
        tls = None
        if has_tls:
            tls = {
                "sni": self._first(fields, "tls.handshake.extensions_server_name"),
                "alpn": self._first(fields, "tls.handshake.extensions_alpn_str"),
                "version": self._first(fields, "tls.record.version", "tls.handshake.version"),
            }
        payload = self._payload(fields, warnings, transforms)
        missing_fields = []
        if src_ip is None:
            missing_fields.append("traffic.representations.packet.network.src_role")
        if dst_ip is None:
            missing_fields.append("traffic.representations.packet.network.dst_role")

        return ParsedTraffic(
            representation_type=self.representation_type,
            representation={
                "representation_type": self.representation_type,
                "protocols": protocols,
                "direction": self._direction(src_role, dst_role),
                "packet_length": packet_length,
                "network": {
                    "ip_version": ip_version_value,
                    "src_role": src_role,
                    "dst_role": dst_role,
                },
                "transport": {
                    "protocol": transport_protocol,
                    "src_port": src_port,
                    "dst_port": dst_port,
                    "tcp_flags": tcp_flags,
                },
                "tls": tls,
                "payload": payload,
            },
            missing_fields=missing_fields,
            warnings=warnings,
            privacy_transforms=list(dict.fromkeys(transforms)),
        )
