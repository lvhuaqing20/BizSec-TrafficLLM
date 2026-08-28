import copy
import json
import unittest
from pathlib import Path

from bizsec_trafficllm.serialization import PromptSerializer, SerializationError
from bizsec_trafficllm.views import TrainingViewGenerator, ViewEngine


class PromptSerializerTests(unittest.TestCase):
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
        cls.generator = TrainingViewGenerator(engine)
        cls.serializer = PromptSerializer(
            root / "schemas",
            json.loads(
                (root / "configs" / "serialization" / "prompt_templates_v1.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        cls.fixtures = root / "tests" / "fixtures" / "canonical" / "valid"

    def fixture(self, name):
        return json.loads((self.fixtures / name).read_text(encoding="utf-8"))

    def test_training_messages_are_deterministic_and_compact(self):
        example = self.generator.build_example(self.fixture("packet_business.json"), "business")
        first = self.serializer.serialize_training(example, "fixture", "train")
        second = self.serializer.serialize_training(example, "fixture", "train")
        self.assertEqual(first, second)
        self.assertEqual([item["role"] for item in first["messages"]], ["system", "user", "assistant"])
        self.assertNotIn("\n", first["messages"][1]["content"])
        self.assertNotIn(": ", first["messages"][2]["content"])

    def test_detection_user_contains_view_but_not_target(self):
        example = self.generator.build_example(self.fixture("http_detection.json"), "detection")
        message = self.serializer.serialize_training(example, "fixture", "test")
        self.assertEqual(json.loads(message["messages"][1]["content"]), example["view"])
        self.assertEqual(json.loads(message["messages"][2]["content"]), {"is_attack": True})
        self.assertNotIn('"is_attack"', message["messages"][1]["content"])

    def test_inference_has_no_assistant_message(self):
        example = self.generator.build_example(self.fixture("http_detection.json"), "detection")
        request = self.serializer.serialize_inference(example["view"], "detection")
        self.assertEqual([item["role"] for item in request["messages"]], ["system", "user"])

    def test_invalid_target_is_rejected(self):
        example = self.generator.build_example(self.fixture("http_detection.json"), "detection")
        invalid = copy.deepcopy(example)
        invalid["target"] = {"is_attack": "yes"}
        with self.assertRaises(SerializationError) as raised:
            self.serializer.serialize_training(invalid, "fixture", "train")
        self.assertEqual(raised.exception.code, "target_schema_invalid")

    def test_attack_type_message_has_no_detection_answer(self):
        example = self.generator.build_example(self.fixture("http_detection.json"), "attack_type")
        message = self.serializer.serialize_training(example, "fixture", "train")
        self.assertNotIn('"is_attack"', message["messages"][1]["content"])
        self.assertEqual(
            json.loads(message["messages"][2]["content"]),
            {"attack_type": "web_attack", "attack_family": None},
        )

    def test_task_view_mismatch_is_rejected(self):
        example = self.generator.build_example(self.fixture("packet_business.json"), "business")
        with self.assertRaises(SerializationError) as raised:
            self.serializer.serialize_inference(example["view"], "detection")
        self.assertEqual(raised.exception.code, "view_schema_invalid")


if __name__ == "__main__":
    unittest.main()
