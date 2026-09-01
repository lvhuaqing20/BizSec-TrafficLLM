import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from bizsec_trafficllm.training.pilot import (
    PilotTrainingError,
    run_pilot_training,
    save_prefix_encoder_checkpoint,
)


class FakeTrainableModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.base = torch.nn.Linear(1, 1)
        self.base.requires_grad_(False)
        self.transformer = torch.nn.Module()
        self.transformer.prefix_encoder = torch.nn.Embedding(4, 3)
        self.checkpointing_enabled = False

    def gradient_checkpointing_enable(self):
        self.checkpointing_enabled = True

    def forward(self, input_ids, attention_mask, labels):
        value = self.transformer.prefix_encoder.weight.mean()
        loss = (value - 1.0).pow(2) + input_ids.float().mean() * 0
        return SimpleNamespace(loss=loss)


class FakeInterface:
    def __init__(self):
        self.model = FakeTrainableModel()
        self.device = "cpu"
        self.task_config = {"task": "detection"}

    def encode_records(self, records):
        record = records[0]
        return {
            "input_ids": torch.tensor([[1, 2]], dtype=torch.long),
            "attention_mask": torch.tensor([[1, 1]], dtype=torch.long),
            "labels": torch.tensor([[-100, 2]], dtype=torch.long),
            "sample_ids": [record["sample_id"]],
            "source_truncated": [False],
            "target_truncated": [False],
        }


class PilotTrainingTests(unittest.TestCase):
    def test_real_optimizer_steps_change_only_prefix(self):
        interface = FakeInterface()
        base_before = interface.model.base.weight.detach().clone()
        records = [{"sample_id": "one"}, {"sample_id": "two"}]
        result = run_pilot_training(
            interface,
            records,
            optimizer_steps=2,
            gradient_accumulation_steps=1,
            learning_rate=0.02,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["optimizer_steps"], 2)
        self.assertGreater(result["parameter_delta_norm"], 0.0)
        self.assertNotEqual(
            result["parameter_digest_before"], result["parameter_digest_after"]
        )
        self.assertTrue(interface.model.checkpointing_enabled)
        self.assertTrue(torch.equal(base_before, interface.model.base.weight))

    def test_optimizer_step_callback_receives_every_step(self):
        reports = []
        run_pilot_training(
            FakeInterface(),
            [{"sample_id": "one"}, {"sample_id": "two"}],
            optimizer_steps=2,
            gradient_accumulation_steps=1,
            learning_rate=0.02,
            optimizer_step_callback=reports.append,
        )
        self.assertEqual([report["optimizer_step"] for report in reports], [1, 2])
        self.assertTrue(all(report["mean_micro_loss"] > 0 for report in reports))

    def test_prefix_checkpoint_is_saved_and_reload_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint" / "pytorch_model.bin"
            metadata = save_prefix_encoder_checkpoint(
                FakeTrainableModel().transformer.prefix_encoder, path
            )
            self.assertTrue(path.is_file())
            self.assertTrue(metadata["reload_verified"])
            self.assertEqual(len(metadata["sha256"]), 64)

    def test_insufficient_records_is_rejected(self):
        with self.assertRaisesRegex(PilotTrainingError, "iterator ended early"):
            run_pilot_training(
                FakeInterface(),
                [{"sample_id": "one"}],
                optimizer_steps=2,
                gradient_accumulation_steps=1,
                learning_rate=0.02,
            )


if __name__ == "__main__":
    unittest.main()
