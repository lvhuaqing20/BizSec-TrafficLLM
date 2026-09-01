import json
import tempfile
import unittest
from pathlib import Path

from bizsec_trafficllm.evaluation import (
    AdapterEvaluationError,
    iter_test_records,
    select_balanced_records,
    summarize_adapter_predictions,
    training_record_to_inference,
)


class AdapterEvaluationTests(unittest.TestCase):
    @staticmethod
    def _record(sample_id, label):
        return {
            "sample_id": sample_id,
            "task": "attack_type",
            "messages": [
                {"role": "system", "content": "classify"},
                {"role": "user", "content": "{}"},
                {
                    "role": "assistant",
                    "content": '{"attack_type":"' + label + '"}',
                },
            ],
            "metadata": {"template_version": "v1"},
        }

    def test_training_record_is_converted_without_assistant_answer(self):
        request, expected = training_record_to_inference(
            {
                "sample_id": "sample",
                "task": "detection",
                "messages": [
                    {"role": "system", "content": "detect"},
                    {"role": "user", "content": "{}"},
                    {"role": "assistant", "content": '{"is_attack":true}'},
                ],
                "metadata": {"template_version": "v1"},
            }
        )
        self.assertEqual(len(request["messages"]), 2)
        self.assertEqual(expected, {"is_attack": True})

    def test_training_record_marks_requested_evaluation_partition(self):
        request, _ = training_record_to_inference(
            {
                "sample_id": "sample",
                "task": "detection",
                "messages": [
                    {"role": "system", "content": "detect"},
                    {"role": "user", "content": "{}"},
                    {"role": "assistant", "content": '{"is_attack":true}'},
                ],
                "metadata": {"template_version": "v1"},
            },
            evaluation_partition="held_out_test",
        )
        self.assertEqual(
            request["metadata"]["evaluation_partition"], "held_out_test"
        )

    def test_iter_test_records_reads_only_declared_test_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "messages" / "v1" / "detection"
            dataset = root / "example"
            dataset.mkdir(parents=True)
            record = {
                "sample_id": "sample",
                "task": "detection",
                "messages": [],
                "metadata": {"split": "test"},
            }
            (dataset / "test.jsonl").write_text(
                json.dumps(record) + "\n", encoding="utf-8"
            )
            self.assertEqual(list(iter_test_records(root, "detection")), [record])

    def test_iter_test_records_rejects_v2_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "messages" / "v2" / "detection"
            root.mkdir(parents=True)
            with self.assertRaisesRegex(AdapterEvaluationError, "excluded v2"):
                list(iter_test_records(root, "detection"))

    def test_detection_metrics_count_invalid_as_false_negative(self):
        summary = summarize_adapter_predictions(
            "detection",
            [
                {
                    "expected": {"is_attack": True},
                    "prediction": {"is_attack": True},
                    "schema_valid": True,
                },
                {
                    "expected": {"is_attack": False},
                    "prediction": {"is_attack": False},
                    "schema_valid": True,
                },
                {
                    "expected": {"is_attack": True},
                    "prediction": None,
                    "schema_valid": False,
                },
            ],
        )
        self.assertEqual(summary["schema_valid_rate"], 2 / 3)
        self.assertEqual(summary["binary"]["tp"], 1)
        self.assertEqual(summary["binary"]["tn"], 1)
        self.assertEqual(summary["binary"]["fn"], 1)
        self.assertEqual(summary["binary"]["invalid"], 1)
        self.assertAlmostEqual(summary["binary"]["f1"], 2 / 3)

    def test_balanced_selection_is_deterministic_and_round_robin(self):
        records = [
            self._record("web-1", "web_attack"),
            self._record("web-2", "web_attack"),
            self._record("web-3", "web_attack"),
            self._record("malware-1", "malware"),
            self._record("malware-2", "malware"),
            self._record("dos-1", "dos"),
        ]
        selected = select_balanced_records("attack_type", records, 5, seed=42)
        repeated = select_balanced_records("attack_type", records, 5, seed=42)
        self.assertEqual(
            [record["sample_id"] for record in selected],
            [record["sample_id"] for record in repeated],
        )
        labels = [
            json.loads(record["messages"][-1]["content"])["attack_type"]
            for record in selected
        ]
        self.assertEqual(set(labels), {"web_attack", "malware", "dos"})
        self.assertLessEqual(max(labels.count(label) for label in set(labels)), 2)


if __name__ == "__main__":
    unittest.main()
