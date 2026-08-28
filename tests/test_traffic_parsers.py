import json
import unittest
from pathlib import Path

from bizsec_trafficllm.data.csic_http_parser import CsicHttpParser
from bizsec_trafficllm.data.direction_sequence_parser import DirectionSequenceParser
from bizsec_trafficllm.data.parser_router import ParserRouter
from bizsec_trafficllm.data.tshark_packet_parser import TsharkPacketParser


class TrafficParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[1]
        cls.privacy = json.loads(
            (project_root / "configs" / "canonical" / "privacy_policy_v1.json").read_text(encoding="utf-8")
        )

    def test_direction_parser_uses_anchored_final_input(self):
        record = {
            "instruction": "Example 11000 and 00111. Input: ：1011001",
            "output": "example.com",
        }
        parsed = DirectionSequenceParser().parse(record)
        self.assertEqual(parsed.representation["sequence"], "1011001")

    def test_csic_parser_splits_url_and_redacts_sensitive_values(self):
        policy = self.privacy["http"]
        parser = CsicHttpParser(
            policy["sensitive_parameter_names_case_insensitive"],
            policy["long_digit_min_length"],
            policy["redaction_tokens"],
        )
        request = {
            "method": "post",
            "url": "/login?username=alice&id=123456789",
            "body": "password=secret&comment=%3Cscript%3Ealert%281%29%3C%2Fscript%3E",
        }
        record = {
            "instruction": f"Task text. {parser.marker}\n {json.dumps(request)}",
            "output": "malicious",
        }
        parsed = parser.parse(record)
        representation = parsed.representation
        self.assertEqual(representation["method"], "POST")
        self.assertEqual(representation["path"], "/login")
        self.assertIn("<REDACTED>", representation["query"])
        self.assertIn("<NUM>", representation["query"])
        self.assertIn("<REDACTED>", representation["body"])
        self.assertIn("script", representation["body"])
        self.assertIn("traffic.representations.http_request.host", parsed.missing_fields)

    def test_tshark_parser_handles_comma_in_time_and_no_packet_colon(self):
        parser = TsharkPacketParser(self.privacy["network"]["internal_cidrs"], 16)
        record = {
            "instruction": (
                "Task mentions <packet> but has no colon.\n"
                "frame.time: Jul 21, 2020 01:59:41 CST, frame.len: 66, "
                "frame.protocols: eth:ip:tcp, ip.version: 4, ip.src: 192.168.1.2, "
                "ip.dst: 8.8.8.8, tcp.srcport: 50000, tcp.dstport: 443, "
                "tcp.flags.syn: 1, tcp.flags.ack: 0, tcp.payload: 00112233445566778899"
            ),
            "output": "app",
        }
        parsed = parser.parse(record)
        representation = parsed.representation
        self.assertEqual(representation["packet_length"], 66)
        self.assertEqual(representation["direction"], "outbound")
        self.assertEqual(representation["transport"]["tcp_flags"], ["SYN"])
        self.assertEqual(representation["payload"]["length"], 10)
        self.assertEqual(len(representation["payload"]["content"]), 16)
        self.assertIn("payload_content_truncated", parsed.warnings)

    def test_router_registers_all_source_mapping_parsers(self):
        router = ParserRouter(self.privacy)
        self.assertEqual(
            router.parser_ids,
            {"tshark_packet_text_v1", "csic_http_json_v1", "binary_direction_sequence_v1"},
        )

    def test_tshark_parser_uses_innermost_values_for_nested_ip(self):
        parser = TsharkPacketParser(self.privacy["network"]["internal_cidrs"], 32)
        record = {
            "instruction": (
                "<packet>: frame.len: 590, frame.protocols: eth:ip:icmp:ip:udp, "
                "ip.version: 4,4, ip.src: 31.13.77.55,192.168.0.150, "
                "ip.dst: 192.168.0.150,31.13.77.55, udp.srcport: 38044, udp.dstport: 443"
            ),
            "output": "app",
        }
        parsed = parser.parse(record)
        self.assertEqual(parsed.representation["network"]["ip_version"], 4)
        self.assertEqual(parsed.representation["network"]["src_role"], "internal")
        self.assertEqual(parsed.representation["network"]["dst_role"], "external")
        self.assertEqual(parsed.representation["direction"], "outbound")
        self.assertIn("multi_layer_ip_uses_innermost", parsed.warnings)


if __name__ == "__main__":
    unittest.main()
