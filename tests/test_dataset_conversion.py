import json
import tempfile
import unittest
from pathlib import Path

from bizsec_trafficllm.data.conversion import DatasetConverter


class DatasetConversionTests(unittest.TestCase):
    @staticmethod
    def _converter(project_root, data_root, source_mapping):
        registry = json.loads(
            (project_root / "configs" / "labels" / "label_registry_v1.json").read_text(encoding="utf-8")
        )
        privacy = json.loads(
            (project_root / "configs" / "canonical" / "privacy_policy_v1.json").read_text(encoding="utf-8")
        )
        return DatasetConverter(
            data_root=data_root,
            schema_root=project_root / "schemas",
            source_mapping=source_mapping,
            label_registry=registry,
            privacy_policy=privacy,
        )

    def test_streaming_conversion_writes_success_and_minimal_failure(self):
        project_root = Path(__file__).resolve().parents[1]
        source_mapping = {
            "datasets": [
                {
                    "dataset_id": "csic-2010",
                    "source_format": "http_request_json",
                    "expected_representation": "http_request",
                    "parser_id": "csic_http_json_v1",
                    "relative_directory": "csic-2010",
                    "file_stem": "csic-2010_detection_packet",
                }
            ]
        }
        valid_record = {
            "instruction": (
                "Classify. The given HTTP request is as follows:\n "
                + json.dumps({"method": "GET", "url": "/index?id=12345678", "body": ""})
            ),
            "output": "benign",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            data_root = temp_root / "data"
            source_dir = data_root / "csic-2010"
            source_dir.mkdir(parents=True)
            source_path = source_dir / "csic-2010_detection_packet_train.json"
            source_path.write_bytes(
                (json.dumps(valid_record, ensure_ascii=False) + "\n").encode("utf-8")
                + b'{"instruction": invalid json}\n'
            )
            converter = self._converter(project_root, data_root, source_mapping)
            output_root = temp_root / "output"
            report = converter.convert_many(["csic-2010"], ["train"], output_root)
            self.assertEqual(report["status"], "completed_with_failures")
            self.assertEqual(report["totals"]["converted"], 1)
            self.assertEqual(report["totals"]["failed"], 1)
            self.assertEqual(report["error_codes"], {"invalid_json": 1})

            success_lines = (output_root / "canonical" / "csic-2010" / "train.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            failure_lines = (output_root / "failures" / "csic-2010" / "train.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(success_lines), 1)
            self.assertEqual(len(failure_lines), 1)
            failure = json.loads(failure_lines[0])
            self.assertNotIn("instruction", failure)
            self.assertEqual(failure["error_code"], "invalid_json")

    def test_hash_stratified_sampling_covers_each_observed_label(self):
        project_root = Path(__file__).resolve().parents[1]
        source_mapping = {
            "datasets": [
                {
                    "dataset_id": "csic-2010",
                    "source_format": "http_request_json",
                    "expected_representation": "http_request",
                    "parser_id": "csic_http_json_v1",
                    "relative_directory": "csic-2010",
                    "file_stem": "csic-2010_detection_packet",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            data_root = temp_root / "data"
            source_dir = data_root / "csic-2010"
            source_dir.mkdir(parents=True)
            source_path = source_dir / "csic-2010_detection_packet_train.json"
            records = []
            for label in ("benign", "malicious"):
                for index in range(6):
                    request = {"method": "GET", "url": f"/item?id={index}", "body": ""}
                    records.append(
                        json.dumps(
                            {
                                "instruction": (
                                    "The given HTTP request is as follows:\n " + json.dumps(request)
                                ),
                                "output": label,
                            }
                        )
                    )
            source_path.write_text("\n".join(records) + "\n", encoding="utf-8")
            converter = self._converter(project_root, data_root, source_mapping)
            output_root = temp_root / "output"
            first = converter.convert_many(
                ["csic-2010"], ["train"], output_root, sample_per_label=2
            )
            self.assertEqual(first["totals"]["converted"], 4)
            self.assertEqual(first["label_coverage"]["csic-2010"]["observed"], 2)
            first_hash = first["runs"][0]["canonical_sha256"]
            second = converter.convert_many(
                ["csic-2010"], ["train"], output_root, sample_per_label=2
            )
            self.assertEqual(second["runs"][0]["canonical_sha256"], first_hash)


if __name__ == "__main__":
    unittest.main()
