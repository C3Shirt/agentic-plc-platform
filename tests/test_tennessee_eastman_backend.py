from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentic_plc.processes import (
    ReadOnlyProcessVariable,
    TennesseeEastmanTraceBackend,
)


class TennesseeEastmanTraceBackendTests(unittest.TestCase):
    def test_loads_te_style_trace_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace_dir = Path(directory)
            _write_matrix(trace_dir / "t.dat", [[0.0, 1.0e30, -1.0], [1.0 / 6.0, 0.0, -1.0]])
            _write_matrix(trace_dir / "y.dat", [_row(1.0, 51), _row(101.0, 51)])
            _write_matrix(trace_dir / "u.dat", [_row(1.0, 12), _row(21.0, 12)])
            _write_matrix(trace_dir / "r.dat", [_row(1.0, 36), _row(201.0, 36)])

            backend = TennesseeEastmanTraceBackend.from_directory(trace_dir)

            snapshot = backend.snapshot()
            self.assertEqual(len(snapshot.measurements), 51)
            self.assertEqual(len(snapshot.manipulated_variables), 12)
            self.assertEqual(len(snapshot.setpoints), 36)
            self.assertEqual(snapshot.read("xmeas_07"), 7.0)
            self.assertEqual(snapshot.read("xmv_10"), 10.0)
            self.assertEqual(snapshot.read("xset_36"), 36.0)

            backend.tick(600.0)
            self.assertEqual(backend.read("xmeas_07"), 107.0)
            self.assertEqual(backend.read("xset_08"), 208.0)

            backend.write("xmv_10", 42.0)
            self.assertEqual(backend.read("xmv_10"), 42.0)
            with self.assertRaises(ReadOnlyProcessVariable):
                backend.write("xmeas_07", 2700.0)


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
