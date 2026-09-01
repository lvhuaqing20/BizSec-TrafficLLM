import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import torch

from bizsec_trafficllm.inference import (
    AdapterCheckpointError,
    load_prefix_encoder_checkpoint,
)


class FakeAdapterModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.transformer = torch.nn.Module()
        self.transformer.prefix_encoder = torch.nn.Embedding(4, 3)


class AdapterCheckpointTests(unittest.TestCase):
    def save_checkpoint(self, directory, state):
        path = Path(directory) / "pytorch_model.bin"
        torch.save(state, path)
        return path

    def save_metadata(self, checkpoint_path, task):
        digest = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
        metadata = {
            "task": task,
            "interface_version": "test-pilot-v1",
            "optimizer_steps": 1,
            "pilot_max_source_length": 4,
            "max_target_length": 2,
            "checkpoint": {"sha256": digest},
        }
        checkpoint_path.with_name("pilot-training-result.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

    def test_exact_prefix_checkpoint_is_loaded_and_verified(self):
        model = FakeAdapterModel()
        expected = torch.full_like(
            model.transformer.prefix_encoder.weight.detach(), 0.25
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(
                directory,
                {"transformer.prefix_encoder.weight": expected},
            )
            report = load_prefix_encoder_checkpoint(model, path)
        self.assertEqual(report["status"], "loaded")
        self.assertTrue(report["post_load_verified"])
        self.assertEqual(report["loaded_parameters"], expected.numel())
        self.assertNotEqual(
            report["parameter_digest_before"], report["parameter_digest_after"]
        )
        self.assertTrue(
            torch.equal(model.transformer.prefix_encoder.weight.detach(), expected)
        )

    def test_unexpected_or_missing_keys_are_rejected(self):
        model = FakeAdapterModel()
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, {"wrong.weight": torch.ones(4, 3)})
            with self.assertRaisesRegex(AdapterCheckpointError, "keys do not match"):
                load_prefix_encoder_checkpoint(model, path)

    def test_shape_mismatch_is_rejected(self):
        model = FakeAdapterModel()
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(
                directory,
                {"transformer.prefix_encoder.weight": torch.ones(2, 3)},
            )
            with self.assertRaisesRegex(AdapterCheckpointError, "shape mismatch"):
                load_prefix_encoder_checkpoint(model, path)

    def test_dtype_mismatch_is_rejected(self):
        model = FakeAdapterModel()
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(
                directory,
                {
                    "transformer.prefix_encoder.weight": torch.ones(
                        4, 3, dtype=torch.float64
                    )
                },
            )
            with self.assertRaisesRegex(AdapterCheckpointError, "dtype mismatch"):
                load_prefix_encoder_checkpoint(model, path)

    def test_non_finite_tensor_is_rejected(self):
        model = FakeAdapterModel()
        value = torch.ones(4, 3)
        value[0, 0] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(
                directory, {"transformer.prefix_encoder.weight": value}
            )
            with self.assertRaisesRegex(AdapterCheckpointError, "non-finite"):
                load_prefix_encoder_checkpoint(model, path)

    def test_task_mismatch_is_rejected_from_training_metadata(self):
        model = FakeAdapterModel()
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(
                directory,
                {
                    "transformer.prefix_encoder.weight": torch.full_like(
                        model.transformer.prefix_encoder.weight.detach(), 0.25
                    )
                },
            )
            self.save_metadata(path, "business")
            with self.assertRaisesRegex(AdapterCheckpointError, "task mismatch"):
                load_prefix_encoder_checkpoint(
                    model, path, expected_task="detection"
                )


if __name__ == "__main__":
    unittest.main()
