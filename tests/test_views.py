import json
import unittest
from pathlib import Path

from bizsec_trafficllm.views import TrainingViewGenerator, ViewConstructionError, ViewEngine


class ViewEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[1]
        config_root = project_root / "configs" / "views"
        cls.engine = ViewEngine(
            schema_root=project_root / "schemas",
            selection_policy=json.loads(
                (config_root / "representation_selection_v1.json").read_text(encoding="utf-8")
            ),
            token_policy=json.loads(
                (config_root / "token_budget_v1.json").read_text(encoding="utf-8")
            ),
        )
        cls.generator = TrainingViewGenerator(cls.engine)
        cls.fixture_root = project_root / "tests" / "fixtures" / "canonical" / "valid"

    def fixture(self, name):
        return json.loads((self.fixture_root / name).read_text(encoding="utf-8"))

    def test_business_direction_view_is_valid_and_label_free(self):
        sample = self.fixture("direction_business.json")
        view = self.engine.build_business(sample)
        self.assertEqual(view["granularity"], "direction_sequence")
        self.assertEqual(view["priors"], {})
        self.assertNotIn("labels", json.dumps(view))

    def test_direction_sequence_is_unavailable_for_detection(self):
        with self.assertRaises(ViewConstructionError) as raised:
            self.engine.build_detection(self.fixture("direction_business.json"), None)
        self.assertEqual(raised.exception.code, "view_unavailable")

    def test_detection_receives_only_structured_business_prior(self):
        sample = self.fixture("http_detection.json")
        prior = {"business_domain": "application", "business_type": "web_service"}
        view = self.engine.build_detection(sample, prior)
        self.assertEqual(view["priors"]["business"], prior)
        self.assertEqual(view["context"]["security"], {"rule_hits": [], "threat_intel_hit": None})

    def test_attack_type_does_not_receive_detection_prior(self):
        view = self.engine.build_attack_type(self.fixture("http_detection.json"), None)
        serialized = json.dumps(view)
        self.assertNotIn("is_attack", serialized)
        self.assertEqual(view["priors"], {"business": None})

    def test_business_payload_uses_task_specific_prelimit(self):
        sample = self.fixture("packet_business.json")
        sample["traffic"]["representations"]["packet"]["payload"] = {
            "length": 400,
            "encoding": "hex",
            "content": "a" * 400,
        }
        view = self.engine.build_business(sample)
        self.assertEqual(len(view["traffic"]["representation"]["payload"]["content"]), 256)
        self.assertIn("view_payload_content_truncated", view["quality"]["warnings"])

    def test_training_example_keeps_target_outside_view(self):
        example = self.generator.build_example(self.fixture("http_detection.json"), "detection")
        self.assertEqual(example["target"], {"is_attack": True})
        self.assertNotIn("is_attack", json.dumps(example["view"]))
        self.assertEqual(example["view"]["priors"], {"business": None})


if __name__ == "__main__":
    unittest.main()
