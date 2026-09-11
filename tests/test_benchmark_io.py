from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentic_plc.evaluation import (
    ConsistencyBenchmarkRunner,
    benchmark_case_from_dict,
    benchmark_cases_from_payload,
    benchmark_cases_to_payload,
    build_default_modbus_consistency_cases,
    load_benchmark_cases,
    write_benchmark_payload,
)


class BenchmarkIOTests(unittest.TestCase):
    def test_round_trips_default_cases_through_json_payload(self) -> None:
        original_cases = build_default_modbus_consistency_cases()
        payload = benchmark_cases_to_payload(original_cases)
        loaded_cases = benchmark_cases_from_payload(payload)

        self.assertEqual(len(loaded_cases), len(original_cases))
        self.assertEqual(loaded_cases[0].case_id, original_cases[0].case_id)
        self.assertEqual(loaded_cases[0].steps[0].step_id, "read_holding_0")
        self.assertEqual(
            loaded_cases[0].steps[0].expected_protocol_status.value,
            "allowed",
        )
        self.assertEqual(
            loaded_cases[2].steps[1].expected_reply_values,
            (700,),
        )

        report = ConsistencyBenchmarkRunner().run(loaded_cases)

        self.assertTrue(report.passed)
        self.assertEqual(report.total_steps, 9)

    def test_loads_direct_case_object(self) -> None:
        case = build_default_modbus_consistency_cases()[0]

        loaded = benchmark_case_from_dict(case.to_dict())

        self.assertEqual(loaded.case_id, case.case_id)
        self.assertEqual(loaded.steps[0].event.operation, "read_holding_registers")

    def test_reads_cases_from_file(self) -> None:
        cases = build_default_modbus_consistency_cases()[:1]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.json"
            write_benchmark_payload(path, benchmark_cases_to_payload(cases))

            loaded = load_benchmark_cases(path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].case_id, "modbus_valid_scan")

    def test_rejects_invalid_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "cases list"):
            benchmark_cases_from_payload({"not_cases": []})

    def test_run_cli_replays_generated_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cases_path = temp_path / "cases.json"
            report_path = temp_path / "report.json"
            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            subprocess.run(
                [
                    sys.executable,
                    "tools/generate_consistency_benchmark.py",
                    "--cases-only",
                    "--output",
                    str(cases_path),
                ],
                check=True,
                capture_output=True,
                env=env,
                text=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "tools/run_consistency_benchmark.py",
                    "--input",
                    str(cases_path),
                    "--output",
                    str(report_path),
                ],
                check=True,
                capture_output=True,
                env=env,
                text=True,
            )
            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIn("report", payload)
        self.assertTrue(payload["report"]["passed"])
        self.assertIn("modbus_write_then_readback", completed.stdout)


if __name__ == "__main__":
    unittest.main()
