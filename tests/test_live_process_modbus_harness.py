from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentic_plc.evaluation import (
    HMIStateObserver,
    LiveModbusBenchmarkRunner,
    build_generated_modbus_attack_cases,
    hmi_register_bindings_from_scenario,
    modbus_attack_options_from_scenario,
    process_snapshot_to_hmi_state,
    start_process_hmi_server,
    start_process_mapped_modbus_server,
)
from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)


class LiveProcessModbusHarnessTests(unittest.TestCase):
    def test_te_scenario_generated_benchmark_passes_against_live_harness(self) -> None:
        scenario_path = Path("scenarios/tennessee_eastman/scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            trace_dir = workspace / "te_trace"
            _write_synthetic_te_trace(trace_dir)
            event_log = workspace / "events.jsonl"

            scenario = ScenarioMapping.from_file(scenario_path)
            backend = TennesseeEastmanTraceBackend.from_directory(
                trace_dir,
                idv="idv1_synthetic",
            )
            backend.tick(600.0)
            register_map = ProcessRegisterMap(backend, scenario)
            modbus = start_process_mapped_modbus_server(
                register_map,
                event_log_path=event_log,
            )
            hmi = start_process_hmi_server(
                backend,
                scenario_id=scenario.scenario_id,
            )
            try:
                options = modbus_attack_options_from_scenario(
                    scenario_path,
                    seed=21,
                    scan_stop=3,
                )
                cases = build_generated_modbus_attack_cases(options)
                report = LiveModbusBenchmarkRunner(
                    host=modbus.host,
                    port=modbus.port,
                    hmi_observer=HMIStateObserver(
                        f"{hmi.url}/api/state",
                        register_bindings=hmi_register_bindings_from_scenario(
                            scenario_path
                        ),
                    ),
                    event_log_path=event_log,
                ).run(cases)
            finally:
                modbus.stop()
                hmi.stop()

        self.assertTrue(report.passed)
        self.assertEqual(report.total_steps, 22)
        self.assertEqual(report.status_counts(), {"allowed": 21, "anomalous": 1})

    def test_hmi_state_flattens_process_snapshot_variables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace_dir = Path(directory) / "te_trace"
            _write_synthetic_te_trace(trace_dir)
            backend = TennesseeEastmanTraceBackend.from_directory(trace_dir)

            state = process_snapshot_to_hmi_state(backend.snapshot())

        self.assertEqual(state["xmeas_07"], 7.0)
        self.assertEqual(state["xmv_10"], 19.0)
        self.assertEqual(state["xset_08"], 107.0)


def _write_synthetic_te_trace(trace_dir: Path) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    _write_matrix(trace_dir / "t.dat", [[0.0], [0.1], [0.2], [0.3]])
    _write_matrix(trace_dir / "y.dat", [_row(float(row), 51) for row in range(1, 5)])
    _write_matrix(trace_dir / "u.dat", [_row(float(row), 12) for row in range(10, 14)])
    _write_matrix(trace_dir / "r.dat", [_row(float(row), 36) for row in range(100, 104)])


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
