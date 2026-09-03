import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from bizsec_trafficllm.training import (
    ChatGLM2TrainingInterface,
    TrainingDataError,
    iter_partition_records,
    select_dataset_label_balanced_records,
)
from bizsec_trafficllm.training.interface import initialize_prefix_encoder


class FakeTokenizer:
    eos_token_id = 2
    pad_token_id = 0

    def build_prompt(self, query, history=None):
        return f"prompt:{query}"

    def encode(self, text, add_special_tokens=False):
        prefix = [9] if add_special_tokens else []
        return prefix + [10 + ord(character) % 20 for character in text]


class FakeModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.base = torch.nn.Linear(1, 1)
        self.transformer = torch.nn.Module()
        self.transformer.prefix_encoder = torch.nn.Embedding(4, 3)

    def forward(self, input_ids, attention_mask, labels):
        loss = input_ids.float().mean() * 0 + torch.tensor(2.5)
        return SimpleNamespace(loss=loss)


def make_record(sample_id, dataset_id="fixture", is_attack=True):
    return {
        "sample_id": sample_id,
        "task": "detection",
        "messages": [
            {"role": "system", "content": "detect"},
            {"role": "user", "content": "{}"},
            {
                "role": "assistant",
                "content": json.dumps({"is_attack": is_attack}),
            },
        ],
        "metadata": {"dataset_id": dataset_id, "split": "train"},
    }


class TrainingInterfaceTests(unittest.TestCase):
    def test_prefix_initialization_is_finite_and_deterministic(self):
        first = torch.nn.Embedding(4, 3)
        second = torch.nn.Embedding(4, 3)
        initialize_prefix_encoder(first, seed=42, std=0.02)
        initialize_prefix_encoder(second, seed=42, std=0.02)
        self.assertTrue(torch.isfinite(first.weight).all())
        self.assertTrue(torch.equal(first.weight, second.weight))

    def test_messages_v1_reader_and_forward_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "messages" / "v1" / "examples" / "detection"
            path = root / "fixture" / "train.jsonl"
            path.parent.mkdir(parents=True)
            records = [make_record(f"sample-{index}") for index in range(20)]
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
            )
            selected = list(
                iter_partition_records(root, "detection", 0.05, 42, "all", limit=2)
            )

        interface = ChatGLM2TrainingInterface(
            FakeModel(),
            FakeTokenizer(),
            {"task": "detection", "max_source_length": 32, "max_target_length": 16},
            "cpu",
        )
        batch = interface.encode_records(selected)
        result = interface.forward_loss(batch)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["batch_size"], 2)
        self.assertEqual(result["loss"], 2.5)
        self.assertGreater(result["trainable_parameters"], 0)
        self.assertFalse(interface.model.base.weight.requires_grad)
        self.assertTrue(interface.model.transformer.prefix_encoder.weight.requires_grad)

    def test_v2_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            forbidden = Path(directory) / "messages" / "v2" / "detection"
            forbidden.mkdir(parents=True)
            with self.assertRaises(TrainingDataError):
                list(iter_partition_records(forbidden, "detection", 0.05, 42))

    def test_messages_reader_can_include_selected_datasets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "messages" / "v1" / "examples" / "detection"
            for dataset_id in ("alpha", "beta"):
                path = root / dataset_id / "train.jsonl"
                path.parent.mkdir(parents=True)
                records = [
                    make_record(
                        f"{dataset_id}-{index}",
                        dataset_id=dataset_id,
                    )
                    for index in range(3)
                ]
                path.write_text(
                    "".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8",
                )
            selected = list(
                iter_partition_records(
                    root,
                    "detection",
                    0.05,
                    42,
                    partition="all",
                    included_dataset_ids=["beta"],
                )
            )
        self.assertEqual(
            [record["metadata"]["dataset_id"] for record in selected],
            ["beta", "beta", "beta"],
        )

    def test_messages_reader_rejects_unknown_included_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "messages" / "v1" / "examples" / "detection"
            path = root / "alpha" / "train.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(make_record("alpha-1", dataset_id="alpha")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TrainingDataError, "not present"):
                list(
                    iter_partition_records(
                        root,
                        "detection",
                        0.05,
                        42,
                        partition="all",
                        included_dataset_ids=["missing"],
                    )
                )

    def test_dataset_label_balanced_sampling_is_reproducible(self):
        records = []
        for dataset_id in ("alpha", "beta", "gamma"):
            for is_attack in (False, True):
                records.extend(
                    make_record(
                        f"{dataset_id}-{is_attack}-{index}",
                        dataset_id=dataset_id,
                        is_attack=is_attack,
                    )
                    for index in range(8)
                )
        selected, audit = select_dataset_label_balanced_records(
            records, "detection", limit=30, seed=42
        )
        repeated, repeated_audit = select_dataset_label_balanced_records(
            reversed(records), "detection", limit=30, seed=42
        )
        self.assertEqual(
            [record["sample_id"] for record in selected],
            [record["sample_id"] for record in repeated],
        )
        self.assertEqual(audit, repeated_audit)
        self.assertEqual(
            audit["selected_dataset_distribution"],
            {"alpha": 10, "beta": 10, "gamma": 10},
        )
        self.assertEqual(
            audit["selected_label_distribution"], {"false": 15, "true": 15}
        )
        self.assertEqual(audit["population_dataset_label_groups"], 6)


if __name__ == "__main__":
    unittest.main()
