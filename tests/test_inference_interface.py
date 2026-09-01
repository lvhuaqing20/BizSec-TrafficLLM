import unittest
from pathlib import Path

import torch

from bizsec_trafficllm.inference import (
    ChatGLM2InferenceInterface,
    InferenceInterfaceError,
    request_to_query,
)


class FakeModel:
    def __init__(self, response):
        self.response = response

    def chat(self, tokenizer, query, history, **kwargs):
        return self.response, history + [(query, self.response)]


class FakeLengthAlignedTokenizer:
    def build_prompt(self, query, history):
        return f"prompt:{query}"

    def encode(self, prompt, add_special_tokens):
        return list(range(8))

    def decode(self, token_ids):
        return '{"is_attack":false}'


class FakeLengthAlignedModel:
    def __init__(self):
        self.observed_source_length = None

    def generate(self, input_ids, **kwargs):
        self.observed_source_length = input_ids.shape[1]
        suffix = torch.tensor([[8, 9]], dtype=torch.long, device=input_ids.device)
        return torch.cat([input_ids, suffix], dim=1)

    def process_response(self, response):
        return response


class InferenceInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema_root = Path(__file__).resolve().parents[1] / "schemas"
        cls.request = {
            "sample_id": "fixture",
            "task": "detection",
            "messages": [
                {"role": "system", "content": "detect"},
                {"role": "user", "content": "{}"},
            ],
        }

    def test_valid_json_is_schema_checked(self):
        interface = ChatGLM2InferenceInterface(
            FakeModel('{"is_attack":true}'), object(), self.schema_root, "cpu"
        )
        result = interface.predict(self.request)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["parsed_output"], {"is_attack": True})
        self.assertTrue(result["schema_valid"])

    def test_raw_non_json_is_preserved(self):
        interface = ChatGLM2InferenceInterface(
            FakeModel("not-json"), object(), self.schema_root, "cpu"
        )
        result = interface.predict(self.request)
        self.assertEqual(result["raw_model_output"], "not-json")
        self.assertIsNone(result["parsed_output"])
        self.assertFalse(result["schema_valid"])

    def test_role_contract_is_strict(self):
        invalid = dict(self.request)
        invalid["messages"] = [{"role": "user", "content": "{}"}]
        with self.assertRaises(InferenceInterfaceError):
            request_to_query(invalid)

    def test_message_objects_are_required(self):
        invalid = dict(self.request)
        invalid["messages"] = ["system", {"role": "user", "content": "{}"}]
        with self.assertRaises(InferenceInterfaceError):
            request_to_query(invalid)

    def test_length_aligned_generation_matches_training_source_limit(self):
        model = FakeLengthAlignedModel()
        interface = ChatGLM2InferenceInterface(
            model, FakeLengthAlignedTokenizer(), self.schema_root, "cpu"
        )
        result = interface.predict(
            self.request, max_length=16, max_source_length=4
        )
        self.assertEqual(model.observed_source_length, 4)
        self.assertEqual(result["source_tokens_raw"], 8)
        self.assertEqual(result["source_tokens_used"], 4)
        self.assertTrue(result["source_truncated"])
        self.assertTrue(result["schema_valid"])


if __name__ == "__main__":
    unittest.main()
