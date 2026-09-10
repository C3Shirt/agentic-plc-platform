from __future__ import annotations

import unittest

from agentic_plc.processes import (
    ProcessVariable,
    ReadOnlyProcessVariable,
    TraceProcessBackend,
    UnknownProcessVariable,
)


class TraceProcessBackendTests(unittest.TestCase):
    def test_trace_ticks_and_reads_current_row(self) -> None:
        backend = TraceProcessBackend(
            process_id="demo",
            name="demo_trace",
            time_seconds=[0.0, 10.0, 20.0],
            variables=[
                ProcessVariable("m1", "Measurement 1", "measurement", "C"),
                ProcessVariable(
                    "u1",
                    "Valve 1",
                    "manipulated_variable",
                    "%",
                    minimum=0.0,
                    maximum=100.0,
                    writable=True,
                ),
                ProcessVariable("sp1", "Setpoint 1", "setpoint", "%", writable=True),
            ],
            series={
                "m1": [1.0, 2.0, 3.0],
                "u1": [10.0, 20.0, 30.0],
                "sp1": [50.0, 51.0, 52.0],
            },
        )

        self.assertEqual(backend.read("m1"), 1.0)
        backend.tick(10.0)
        snapshot = backend.snapshot()
        self.assertEqual(snapshot.read("m1"), 2.0)
        self.assertEqual(snapshot.manipulated_variables["u1"], 20.0)
        self.assertEqual(snapshot.setpoints["sp1"], 51.0)
        self.assertEqual(snapshot.metadata["row_index"], 1)

    def test_writes_are_bounded_runtime_overrides(self) -> None:
        backend = TraceProcessBackend(
            process_id="demo",
            name="demo_trace",
            time_seconds=[0.0, 10.0],
            variables=[
                ProcessVariable("m1", "Measurement 1", "measurement"),
                ProcessVariable(
                    "u1",
                    "Valve 1",
                    "manipulated_variable",
                    "%",
                    minimum=0.0,
                    maximum=100.0,
                    writable=True,
                ),
            ],
            series={"m1": [1.0, 2.0], "u1": [10.0, 20.0]},
        )

        with self.assertRaises(ReadOnlyProcessVariable):
            backend.write("m1", 3.0)
        with self.assertRaises(ValueError):
            backend.write("u1", 150.0)

        backend.write("u1", 42.0)
        self.assertEqual(backend.read("u1"), 42.0)
        self.assertEqual(backend.snapshot().metadata["overrides"], {"u1": 42.0})
        backend.reset()
        self.assertEqual(backend.read("u1"), 10.0)

    def test_unknown_variable_is_rejected(self) -> None:
        backend = TraceProcessBackend(
            process_id="demo",
            name="demo_trace",
            time_seconds=[0.0],
            variables=[ProcessVariable("m1", "Measurement 1", "measurement")],
            series={"m1": [1.0]},
        )

        with self.assertRaises(UnknownProcessVariable):
            backend.read("missing")


if __name__ == "__main__":
    unittest.main()
