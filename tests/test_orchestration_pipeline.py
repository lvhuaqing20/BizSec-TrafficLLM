import copy
import json
import unittest
from pathlib import Path
from typing import Any, Mapping, Optional

from bizsec_trafficllm.orchestration import (
    OrchestrationError,
    SerialInferencePipeline,
)
from bizsec_trafficllm.serialization import PromptSerializer
from bizsec_trafficllm.views import ViewEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ScriptedBackend:
    name = "test-adapter-backend"

    def __init__(self, outputs):
        self.outputs = copy.deepcopy(outputs)
        self.calls = []
        self.requests = {}

    def predict(self, task, request):
        self.calls.append(task)
        self.requests[task] = copy.deepcopy(request)
        return copy.deepcopy(self.outputs[task])


class StubFusion:
    name = "test-risk-fusion"

    def __init__(self, invalid=False):
        self.invalid = invalid
        self.calls = []

    def fuse(
        self,
        *,
        business_output: Mapping[str, Any],
        detection_output: Mapping[str, Any],
        attack_type_output: Optional[Mapping[str, Any]],
    ):
        self.calls.append(
            {
                "business": copy.deepcopy(business_output),
                "detection": copy.deepcopy(detection_output),
                "attack_type": copy.deepcopy(attack_type_output),
            }
        )
        if self.invalid:
            return {
                "risk_score": 2.0,
                "risk_level": "low",
                "evidence": [],
                "recommended_action": [],
            }
        if detection_output["is_attack"]:
            return {
                "risk_score": 0.9,
                "risk_level": "critical",
                "evidence": ["detection.is_attack=true", "attack_type=malware"],
                "recommended_action": ["isolate_asset"],
            }
        return {
            "risk_score": 0.05,
            "risk_level": "low",
            "evidence": ["detection.is_attack=false"],
            "recommended_action": ["continue_monitoring"],
        }


class OrchestrationPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = load_json(
            PROJECT_ROOT / "tests/fixtures/canonical/valid/live_multi_representation.json"
        )
        cls.selection = load_json(
            PROJECT_ROOT / "configs/views/representation_selection_v1.json"
        )
        cls.budget = load_json(PROJECT_ROOT / "configs/views/token_budget_v1.json")
        cls.prompts = load_json(
            PROJECT_ROOT / "configs/serialization/prompt_templates_v1.json"
        )

    def make_pipeline(self, outputs, fusion=None):
        backend = ScriptedBackend(outputs)
        pipeline = SerialInferencePipeline(
            ViewEngine(
                PROJECT_ROOT / "schemas", self.selection, self.budget
            ),
            PromptSerializer(PROJECT_ROOT / "schemas", self.prompts),
            backend,
            fusion or StubFusion(),
            PROJECT_ROOT / "schemas",
        )
        return pipeline, backend

    @staticmethod
    def outputs(is_attack=True):
        return {
            "business": {
                "business_domain": "application",
                "business_type": "order_service",
            },
            "detection": {"is_attack": is_attack},
            "attack_type": {
                "attack_type": "malware",
                "attack_family": "Cridex",
            },
        }

    def test_attack_branch_calls_all_tasks_and_injects_prior(self):
        pipeline, backend = self.make_pipeline(self.outputs(True))
        original = copy.deepcopy(self.sample)
        run = pipeline.run(self.sample, request_id="attack-request")

        self.assertEqual(backend.calls, ["business", "detection", "attack_type"])
        self.assertTrue(run["gate"]["is_attack"])
        self.assertTrue(run["gate"]["attack_type_called"])
        self.assertEqual(run["result"]["attack_type"], "malware")
        self.assertEqual(run["result"]["risk_level"], "critical")
        prior = {"business_domain": "application", "business_type": "order_service"}
        self.assertEqual(run["stages"][1]["business_prior"], prior)
        self.assertEqual(run["stages"][2]["business_prior"], prior)
        self.assertIn(
            '"business":{"business_domain":"application","business_type":"order_service"}',
            backend.requests["detection"]["messages"][1]["content"],
        )
        self.assertEqual(self.sample, original)

    def test_benign_branch_skips_attack_type(self):
        pipeline, backend = self.make_pipeline(self.outputs(False))
        run = pipeline.run(self.sample, request_id="benign-request")

        self.assertEqual(backend.calls, ["business", "detection"])
        self.assertFalse(run["gate"]["attack_type_called"])
        self.assertEqual(run["result"]["attack_type"], "benign")
        self.assertEqual(run["result"]["risk_score"], 0.05)
        self.assertEqual(len(run["stages"]), 2)

    def test_invalid_adapter_output_is_rejected(self):
        outputs = self.outputs(True)
        outputs["detection"] = {"is_attack": "yes"}
        pipeline, _ = self.make_pipeline(outputs)
        with self.assertRaisesRegex(OrchestrationError, "detection") as context:
            pipeline.run(self.sample)
        self.assertEqual(context.exception.code, "adapter_output_invalid")

    def test_invalid_fusion_output_is_rejected_by_pipeline_schema(self):
        pipeline, _ = self.make_pipeline(self.outputs(False), StubFusion(invalid=True))
        with self.assertRaises(OrchestrationError) as context:
            pipeline.run(self.sample)
        self.assertEqual(context.exception.code, "pipeline_result_invalid")

    def test_default_request_id_is_deterministic(self):
        first, _ = self.make_pipeline(self.outputs(False))
        second, _ = self.make_pipeline(self.outputs(False))
        first_result = first.run(self.sample)["result"]
        second_result = second.run(self.sample)["result"]
        self.assertEqual(first_result["request_id"], second_result["request_id"])
        self.assertEqual(first_result, second_result)


if __name__ == "__main__":
    unittest.main()
