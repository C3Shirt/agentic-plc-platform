from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentic_plc.evaluation import (
    CICModbusImportOptions,
    ConsistencyBenchmarkRunner,
    import_cic_modbus_benchmark,
    recommended_tshark_command,
    recommended_tshark_fields,
)


class CICModbusImporterTests(unittest.TestCase):
    def test_imports_tshark_csv_with_attack_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            packets = temp_path / "packets.csv"
            attacks = temp_path / "attacks.csv"
            packets.write_text(
                "\n".join(
                    [
                        (
                            "frame.number,frame.time_epoch,ip.src,tcp.srcport,"
                            "ip.dst,tcp.dstport,tcp.stream,mbtcp.trans_id,"
                            "mbtcp.unit_id,modbus.func_code,modbus.reference_num,"
                            "modbus.word_cnt,direction"
                        ),
                        (
                            "1,1700000000.0,10.0.0.8,55000,10.0.0.20,502,7,"
                            "10,1,3,0,1,request"
                        ),
                        (
                            "2,1700000000.1,10.0.0.20,502,10.0.0.8,55000,7,"
                            "10,1,3,0,1,response"
                        ),
                        (
                            "3,1700000000.2,10.0.0.8,55000,10.0.0.20,502,7,"
                            "10,1,3,2,1,request"
                        ),
                        (
                            "4,1700000000.3,10.0.0.8,55000,10.0.0.20,502,7,"
                            "11,1,99,0,1,request"
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            attacks.write_text(
                "\n".join(
                    [
                        "Timestamp,TargetIP,Attack,TransactionID",
                        "1700000000.0,10.0.0.20,Reconnaissance,10",
                        "1700000000.3,10.0.0.20,Stacking Modbus Frames,11",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            case = import_cic_modbus_benchmark(
                packets,
                attack_log_csv=attacks,
                options=CICModbusImportOptions(case_id="fixture", max_rows=10),
            )

        self.assertEqual(case.case_id, "fixture")
        self.assertFalse(case.requires_process_context)
        self.assertEqual(len(case.steps), 3)
        self.assertIn("attack_labeled", case.tags)
        self.assertIn("responses_filtered", case.tags)
        self.assertEqual(case.steps[0].request_hex, "000a00000006010300000001")
        self.assertEqual(case.steps[0].expected_protocol_status.value, "allowed")
        self.assertEqual(case.steps[1].expected_protocol_status.value, "anomalous")
        self.assertEqual(case.steps[2].expected_protocol_status.value, "denied")
        self.assertFalse(case.steps[2].expected_reply_generated)
        self.assertEqual(case.steps[0].event.metadata["cic_attack"], "Reconnaissance")
        self.assertEqual(
            case.steps[2].event.metadata["cic_attack"],
            "Stacking Modbus Frames",
        )

    def test_imported_case_runs_as_protocol_fsm_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            packets = Path(temp_dir) / "packets.csv"
            packets.write_text(
                "\n".join(
                    [
                        (
                            "payload_hex,ip.src,ip.dst,tcp.stream,"
                            "direction,protocol"
                        ),
                        (
                            "000100000006010300000001,192.0.2.10,"
                            "192.0.2.20,1,request,Modbus"
                        ),
                        (
                            "000100000006010300010001,192.0.2.10,"
                            "192.0.2.20,1,request,Modbus"
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            case = import_cic_modbus_benchmark(packets)
            report = ConsistencyBenchmarkRunner().run((case,))

        self.assertTrue(report.passed)
        self.assertEqual(report.status_counts(), {"allowed": 1, "anomalous": 1})
        second_result = report.cases[case.case_id][1]
        self.assertTrue(second_result.passed)
        self.assertTrue(second_result.checks["protocol_status"])

    def test_importer_can_expect_duplicate_transaction_anomaly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            packets = Path(temp_dir) / "packets.csv"
            packets.write_text(
                "\n".join(
                    [
                        (
                            "mbtcp.trans_id,mbtcp.unit_id,modbus.func_code,"
                            "modbus.reference_num,modbus.word_cnt"
                        ),
                        "20,1,3,0,1",
                        "20,1,3,2,1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            case = import_cic_modbus_benchmark(packets)
            report = ConsistencyBenchmarkRunner().run((case,))

        self.assertTrue(report.passed)
        self.assertEqual(case.steps[1].expected_protocol_status.value, "anomalous")

    def test_recommended_tshark_recipe_is_exposed(self) -> None:
        fields = recommended_tshark_fields()
        command = recommended_tshark_command("sample.pcap", "modbus.csv")

        self.assertIn("mbtcp.trans_id", fields)
        self.assertIn("-Y modbus", command)
        self.assertIn("sample.pcap", command)

    def test_cli_imports_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            packets = temp_path / "packets.csv"
            output = temp_path / "out.json"
            packets.write_text(
                "\n".join(
                    [
                        (
                            "mbtcp.trans_id,mbtcp.unit_id,modbus.func_code,"
                            "modbus.reference_num,modbus.word_cnt"
                        ),
                        "30,1,3,0,1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            completed = subprocess.run(
                [
                    sys.executable,
                    "tools/import_cic_modbus_benchmark.py",
                    "--packets",
                    str(packets),
                    "--output",
                    str(output),
                    "--run",
                ],
                check=True,
                capture_output=True,
                env=env,
                text=True,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("cases", payload)
        self.assertIn("report", payload)
        self.assertIn("cic_modbus_import", completed.stdout)


if __name__ == "__main__":
    unittest.main()
