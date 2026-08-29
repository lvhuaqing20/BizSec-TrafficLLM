import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
