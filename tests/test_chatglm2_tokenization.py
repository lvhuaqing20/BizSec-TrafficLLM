import copy
import json
import unittest
from pathlib import Path

from bizsec_trafficllm.serialization import PromptSerializer
from bizsec_trafficllm.tokenization import (
    ChatGLM2FeatureAdapter,
    MessageFormatError,
    audit_messages,
    message_to_text_pair,
)
from bizsec_trafficllm.views import TrainingViewGenerator, ViewEngine


class FakeChatGLM2Tokenizer:
    eos_token_id = 2
    pad_token_id = 0

    def build_prompt(self, query, history=None):
        if history is not None:
            raise AssertionError("history must remain disabled")
        return f"[Round 1]\n\n问：{query}\n\n答："

    def encode(self, text, add_special_tokens=False):
        prefix = [101, 102] if add_special_tokens else []
        return prefix + [10 + (ord(character) % 80) for character in text]


class ChatGLM2TokenizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        view_config = root / "configs" / "views"
        engine = ViewEngine(
            schema_root=root / "schemas",
            selection_policy=json.loads(
                (view_config / "representation_selection_v1.json").read_text(encoding="utf-8")
            ),
            token_policy=json.loads(
                (view_config / "token_budget_v1.json").read_text(encoding="utf-8")
            ),
        )
        generator = TrainingViewGenerator(engine)
        serializer = PromptSerializer(
            root / "schemas",
            json.loads(
                (root / "configs/serialization/prompt_templates_v1.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        canonical = json.loads(
            (root / "tests/fixtures/canonical/valid/http_detection.json").read_text(
                encoding="utf-8"
            )
        )
        example = generator.build_example(canonical, "detection")
        cls.record = serializer.serialize_training(example, "fixture", "train")
        cls.tokenizer = FakeChatGLM2Tokenizer()

    def test_message_pair_embeds_system_and_keeps_answer_separate(self):
        pair = message_to_text_pair(self.record)
        self.assertIn(self.record["messages"][0]["content"], pair.query)
        self.assertIn("Traffic view:\n", pair.query)
        self.assertEqual(pair.response, self.record["messages"][2]["content"])
        self.assertNotIn(pair.response, pair.query)

    def test_roles_are_strict(self):
        invalid = copy.deepcopy(self.record)
        invalid["messages"][1]["role"] = "assistant"
        with self.assertRaises(MessageFormatError):
            message_to_text_pair(invalid)

    def test_feature_mask_covers_prompt_and_padding(self):
        adapter = ChatGLM2FeatureAdapter(self.tokenizer, 80, 20)
        feature = adapter.encode(self.record)
        self.assertEqual(len(feature["input_ids"]), 101)
        self.assertEqual(len(feature["labels"]), 101)
        self.assertTrue(all(value == -100 for value in feature["labels"][:80]))
        target_positions = [value for value in feature["labels"] if value != -100]
        self.assertEqual(target_positions[-1], self.tokenizer.eos_token_id)
        self.assertNotIn(self.tokenizer.pad_token_id, target_positions)
        self.assertTrue(feature["source_truncated"])

    def test_token_audit_reports_truncation(self):
        report = audit_messages(
            [self.record],
            self.tokenizer,
            {"detection": {"max_source_length": 20, "max_target_length": 2}},
        )
        self.assertEqual(report["records"], 1)
        self.assertEqual(report["tasks"]["detection"]["truncation"]["source_records"], 1)
        self.assertEqual(report["tasks"]["detection"]["truncation"]["target_records"], 1)


if __name__ == "__main__":
    unittest.main()
