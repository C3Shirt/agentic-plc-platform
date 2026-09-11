from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)
from agentic_plc.world.registers import RegisterAccessError, RegisterArea


class ProcessRegisterMapTests(unittest.TestCase):
    def test_te_scenario_reads_and_writes_modbus_registers(self) -> None:
        backend, registers = _te_register_map()

        self.assertEqual(
            registers.read(RegisterArea.INPUT_REGISTERS, 0, 3),
            [7, 80, 90],
        )
        write = registers.write(RegisterArea.HOLDING_REGISTERS, 5, 420)

        self.assertEqual(write.previous_value, 100)
        self.assertEqual(write.resulting_value, 420)
        self.assertEqual(backend.read("xmv_10"), 42.0)
        with self.assertRaises(RegisterAccessError):
            registers.write(RegisterArea.INPUT_REGISTERS, 0, 2700)

    def test_preview_write_decodes_without_mutating_process_backend(self) -> None:
        backend, registers = _te_register_map()

        plan = registers.preview_write(RegisterArea.HOLDING_REGISTERS, 5, 420)

        self.assertEqual(plan.variable_id, "xmv_10")
        self.assertEqual(plan.requested_value, 420)
        self.assertEqual(plan.engineering_value, 42.0)
        self.assertEqual(backend.read("xmv_10"), 10.0)


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _te_register_map() -> tuple[TennesseeEastmanTraceBackend, ProcessRegisterMap]:
    with tempfile.TemporaryDirectory() as directory:
        trace_dir = Path(directory)
        _write_matrix(trace_dir / "t.dat", [[0.0, 1.0e30, -1.0]])
        _write_matrix(trace_dir / "y.dat", [_row(1.0, 51)])
        _write_matrix(trace_dir / "u.dat", [_row(1.0, 12)])
        _write_matrix(trace_dir / "r.dat", [_row(1.0, 36)])
        backend = TennesseeEastmanTraceBackend.from_directory(trace_dir)
    scenario = ScenarioMapping.from_file(
        Path("scenarios/tennessee_eastman/scenario.json")
    )
    return backend, ProcessRegisterMap(backend, scenario)


if __name__ == "__main__":
    unittest.main()
