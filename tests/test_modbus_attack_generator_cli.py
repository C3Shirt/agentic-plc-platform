from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ModbusAttackGeneratorCliTests(unittest.TestCase):
    def test_cli_writes_quiet_generated_benchmark_payload(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "generated_modbus_attack.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/generate_live_modbus_attack_benchmark.py",
                    "--seed",
                    "5",
                    "--scan-stop",
                    "3",
                    "--output",
                    str(output_path),
                    "--quiet",
                ],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.stdout, "")
        self.assertEqual(payload["metadata"]["seed"], 5)
        self.assertEqual(len(payload["cases"]), 6)
        self.assertEqual(
            sum(len(case["steps"]) for case in payload["cases"]),
            37,
        )


if __name__ == "__main__":
    unittest.main()
