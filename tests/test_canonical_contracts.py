import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CanonicalContractTests(unittest.TestCase):
    def test_phase3_canonical_contracts(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "canonical-contract-validation.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(project_root / "scripts" / "validate_canonical_contracts.py"),
                    "--schema-root",
                    str(project_root / "schemas"),
                    "--config-root",
                    str(project_root / "configs" / "canonical"),
                    "--fixtures-dir",
                    str(project_root / "tests" / "fixtures" / "canonical"),
                    "--registry",
                    str(project_root / "configs" / "labels" / "label_registry_v1.json"),
                    "--audit",
                    str(project_root / "reports" / "phase1" / "dataset_audit_v1.json"),
                    "--report",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(report.exists())


if __name__ == "__main__":
    unittest.main()
