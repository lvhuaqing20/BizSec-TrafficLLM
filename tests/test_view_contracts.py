import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ViewContractTests(unittest.TestCase):
    def test_phase2_view_contracts(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "view-contract-validation.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(project_root / "scripts" / "validate_view_contracts.py"),
                    "--schema-root",
                    str(project_root / "schemas"),
                    "--config-root",
                    str(project_root / "configs" / "views"),
                    "--fixtures-dir",
                    str(project_root / "tests" / "fixtures" / "views"),
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
