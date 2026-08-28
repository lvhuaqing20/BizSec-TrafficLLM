import json
import unittest
from pathlib import Path

from bizsec_trafficllm.data.canonical_builder import CanonicalSampleBuilder
from bizsec_trafficllm.data.canonical_validation import CanonicalValidator
from bizsec_trafficllm.data.label_resolver import LabelResolver
from bizsec_trafficllm.data.models import ParsedTraffic


class CanonicalBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]
        registry = json.loads(
            (cls.project_root / "configs" / "labels" / "label_registry_v1.json").read_text(encoding="utf-8")
        )
        cls.resolver = LabelResolver(registry)
        cls.builder = CanonicalSampleBuilder(cls.resolver)
        cls.validator = CanonicalValidator(cls.project_root / "schemas")

    def test_label_resolver_applies_registry_normalization(self):
        labels = self.resolver.resolve("cw100-2018", "gfycat.com。")
        self.assertEqual(labels["raw"]["normalized_value"], "gfycat.com")
        self.assertEqual(labels["eligible_tasks"], ["business"])

    def test_builder_is_deterministic_and_schema_valid(self):
        raw_line = '{"instruction":"Input: 1010","output":"gfycat.com。"}\n'.encode("utf-8")
        parsed = ParsedTraffic(
            representation_type="direction_sequence",
            representation={
                "representation_type": "direction_sequence",
                "encoding": "binary",
                "sequence": "1010",
            },
        )
        kwargs = {
            "dataset_id": "cw100-2018",
            "split": "train",
            "source_file": "cw100-2018-2024/cw100-2018_detection_packet_train.json",
            "record_index": 0,
            "source_format": "direction_bit_sequence",
            "raw_record_bytes": raw_line,
            "decoded_record": {"instruction": "Input: 1010", "output": "gfycat.com。"},
            "parsed": parsed,
        }
        first = self.builder.build_dataset_sample(**kwargs)
        second = self.builder.build_dataset_sample(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(
            first["sample_id"],
            "056051c87f925f0e01f1c8d8e15da54d420e11ffb9d5dd8ad4cba27d1bca1dcd",
        )
        self.assertEqual(self.validator.issues(first), [])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn('"instruction"', serialized)


if __name__ == "__main__":
    unittest.main()
