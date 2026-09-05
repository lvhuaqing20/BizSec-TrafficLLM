"""CPU-only checks for the published Business experiment review package."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "experiments"
PAPER = EXPERIMENTS / "paper-style"
REDUCED = EXPERIMENTS / "reduced-labels"


class ExperimentPublicationTests(unittest.TestCase):
    def test_saved_paper_metrics_consistent(self):
        paths = sorted((PAPER / "results").glob("*-validation400.json"))
        self.assertEqual(len(paths), 15)
        for path in paths:
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["samples"], 400)
            matrix = result["confusion_matrix"]
            self.assertEqual(sum(map(sum, matrix)), 400)
            correct = sum(row[i] for i, row in enumerate(matrix))
            self.assertAlmostEqual(result["accuracy"], correct / 400)
            self.assertEqual(result["labels"], 20)
        last = json.loads((PAPER / "results/full-6000-validation400.json").read_text())
        self.assertEqual(last["accuracy"], 0.625)

    def test_reduced_scope_and_curve(self):
        config = json.loads((REDUCED / "config.json").read_text())
        script = (REDUCED / "scripts/train.sh").read_text()
        self.assertEqual(
            sorted(re.findall(r"--include-dataset ([a-z0-9-]+)", script)),
            sorted(config["included_datasets"]),
        )
        self.assertEqual(config["global_labels_after"], 150)
        curve = json.loads((REDUCED / "results/checkpoint-curve-summary.json").read_text())
        self.assertEqual(len(curve["curve"]), 20)
        best = max(curve["curve"], key=lambda row: (row["macro_f1"], row["accuracy"]))
        self.assertEqual(best["step"], 19000)
        self.assertEqual(best, curve["best_checkpoint"])
        self.assertTrue(curve["sample_identity"]["all_checkpoints_match_old_baseline"])

    def test_converter_preserves_prompt_and_deterministic_split(self):
        converter = PAPER / "scripts/prepare_bizsec_cstnet_paper_data.py"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            records = []
            for label in ("synthetic_a", "synthetic_b"):
                for i in range(20):
                    records.append({"sample_id": f"{label}-{i}", "messages": [
                        {"role": "system", "content": "synthetic test only"},
                        {"role": "user", "content": '{"packet_length":64}'},
                        {"role": "assistant", "content": json.dumps({
                            "business_domain": "application", "business_type": label})},
                    ]})
            train, test = directory / "train.jsonl", directory / "test.jsonl"
            for path, rows in ((train, records[:20]), (test, records[20:])):
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            for out in (directory / "one", directory / "two"):
                subprocess.run([sys.executable, str(converter), "--train", str(train),
                                "--test", str(test), "--output-dir", str(out)],
                               check=True, capture_output=True, text=True)
            manifest = json.loads((directory / "one/split_manifest.json").read_text())
            self.assertEqual(manifest["cross_split_overlap"], {
                "train_validation": 0, "train_test": 0, "validation_test": 0})
            self.assertEqual([manifest["outputs"][s]["records"] for s in ("train", "validation", "test")], [32, 4, 4])
            for split in ("train", "validation", "test"):
                name = f"bizsec_cstnet_business_{split}.json"
                self.assertEqual((directory / "one" / name).read_bytes(), (directory / "two" / name).read_bytes())
            first = json.loads((directory / "one/bizsec_cstnet_business_train.json").read_text().splitlines()[0])
            self.assertEqual(first["instruction"], 'synthetic test only\n\nTraffic view:\n{"packet_length":64}')

    def test_converter_rejects_excluded_path_without_reading(self):
        spec = importlib.util.spec_from_file_location("paper_data_converter", PAPER / "scripts/prepare_bizsec_cstnet_paper_data.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module.reject_v2(Path("excluded/v2/never_open.jsonl"))

    @unittest.skipIf(os.name == "nt", "shell checks run on the Linux publication checkout")
    def test_shell_syntax_without_executing_training(self):
        for script in EXPERIMENTS.rglob("*.sh"):
            subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)

    def test_no_runtime_payloads_in_package(self):
        for path in EXPERIMENTS.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            self.assertNotIn(path.suffix, (".bin", ".pt", ".pth", ".safetensors", ".pcap", ".zip", ".jsonl"))
            self.assertLess(path.stat().st_size, 1_000_000)
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"/mnt/\d+T/[^/\s]+/")
            self.assertNotRegex(text, r"connect\.[a-z0-9.-]+\.seetacloud\.com")


if __name__ == "__main__":
    unittest.main()
