import unittest

from bizsec_trafficllm.orchestration import (
    AdapterBackendError,
    ChatGLM2AdapterBackend,
    DeterministicRiskFusionBackend,
)


class FakeInterface:
    def __init__(self, task, output, schema_valid=True):
        self.task = task
        self.output = output
        self.schema_valid = schema_valid
        self.device = "cpu"
        self.calls = []
        self.adapter_checkpoint = {
            "sha256": f"sha-{task}",
            "training_metadata": {
                "max_source_length": 4,
                "max_target_length": 2,
            },
        }

    def predict(self, request, max_source_length, max_length):
        self.calls.append((request, max_source_length, max_length))
        return {
            "inference_seconds": 0.1,
            "source_tokens_raw": 8,
            "source_tokens_used": 4,
            "source_truncated": True,
            "raw_model_output": "{}",
            "parsed_output": self.output,
            "schema_valid": self.schema_valid,
            "schema_error": None if self.schema_valid else "invalid",
            "json_parse_error": None,
        }


class RealOrchestrationBackendTests(unittest.TestCase):
    def interfaces(self, detection_valid=True):
        return {
            "business": FakeInterface(
                "business",
                {"business_domain": "application", "business_type": "demo"},
            ),
            "detection": FakeInterface(
                "detection", {"is_attack": False}, detection_valid
            ),
            "attack_type": FakeInterface(
                "attack_type",
                {"attack_type": "web_attack", "attack_family": None},
            ),
        }

    def test_backend_uses_checkpoint_lengths_and_returns_parsed_output(self):
        interfaces = self.interfaces()
        backend = ChatGLM2AdapterBackend(interfaces)
        request = {"task": "detection", "sample_id": "sample"}
        output = backend.predict("detection", request)
        self.assertEqual(output, {"is_attack": False})
        self.assertEqual(interfaces["detection"].calls[0][1:], (4, 7))
        self.assertEqual(backend.calls[0]["checkpoint_sha256"], "sha-detection")

    def test_backend_rejects_schema_invalid_model_output(self):
        backend = ChatGLM2AdapterBackend(self.interfaces(detection_valid=False))
        with self.assertRaisesRegex(AdapterBackendError, "invalid output"):
            backend.predict("detection", {"task": "detection", "sample_id": "x"})

    def test_fusion_covers_benign_and_attack_branches(self):
        fusion = DeterministicRiskFusionBackend()
        business = {"business_domain": "application", "business_type": "demo"}
        benign = fusion.fuse(
            business_output=business,
            detection_output={"is_attack": False},
            attack_type_output=None,
        )
        self.assertEqual(benign["risk_level"], "low")
        attack = fusion.fuse(
            business_output=business,
            detection_output={"is_attack": True},
            attack_type_output={"attack_type": "malware", "attack_family": "X"},
        )
        self.assertEqual(attack["risk_level"], "critical")
        self.assertIn("attack_family=X", attack["evidence"])


if __name__ == "__main__":
    unittest.main()
