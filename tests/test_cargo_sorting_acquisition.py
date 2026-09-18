from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from agentic_plc.acquisition import (
    build_cargo_sorting_acquisition_plan,
    learn_process_model,
    load_modbus_trace_jsonl,
    parse_modbus_interaction,
    respond_with_register_map,
    write_modbus_trace_jsonl,
)
from agentic_plc.processes import (
    CargoSortingProcessBackend,
    ProcessRegisterMap,
    ScenarioMapping,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_read_request,
    build_modbus_tcp_write_single_coil_request,
)


class CargoSortingAcquisitionTests(unittest.TestCase):
    def test_plan_exports_modbus_scenario_and_plc_skeleton(self) -> None:
        plan = build_cargo_sorting_acquisition_plan()
        scenario = plan.scenario_mapping()

        self.assertEqual(plan.scenario_id, "cargo_sorting_height_v1")
        self.assertEqual(scenario.backend_type, "cargo_sorting")
        self.assertIn("CLASSIFYING", plan.plc_states)
        self.assertIsNotNone(plan.plc_program)
        assert plan.plc_program is not None
        self.assertIn("ROUTE_RIGHT", plan.plc_program.code)

        points = {(point.table, point.address): point.variable_id for point in scenario.points}
        self.assertEqual(points[("coils", 4)], "turntable")
        self.assertEqual(points[("discrete_inputs", 24)], "high_box")
        self.assertEqual(points[("input_registers", 0)], "box_position")

    def test_static_cargo_sorting_scenario_loads(self) -> None:
        scenario = ScenarioMapping.from_file(Path("scenarios/cargo_sorting/scenario.json"))

        self.assertEqual(scenario.scenario_id, "cargo_sorting_height_v1")
        self.assertEqual(scenario.process_id, "cargo_sorting_height_process")
        self.assertIn(
            "turntable",
            {point.variable_id for point in scenario.variables_for_protocol("modbus")},
        )

    def test_headless_backend_moves_high_box_to_right_lane(self) -> None:
        backend = CargoSortingProcessBackend()

        backend.write("feeder_conveyor", 1.0)
        backend.write("entry_conveyor", 1.0)
        backend.write("right_conveyor", 1.0)
        backend.tick(2.0)

        self.assertEqual(backend.read("box_present"), 1.0)
        self.assertEqual(backend.read("box_height_class"), 2.0)
        self.assertEqual(backend.read("high_box"), 1.0)

        backend.write("turntable", 1.0)
        backend.tick(4.0)

        self.assertGreaterEqual(backend.read("sorted_right_count"), 1.0)

    def test_modbus_trace_learning_builds_replayable_model(self) -> None:
        plan = build_cargo_sorting_acquisition_plan()
        scenario = plan.scenario_mapping()
        backend = CargoSortingProcessBackend()
        registers = ProcessRegisterMap(backend, scenario)
        events = []
        timestamp = 0.0

        def interact(request_hex: str, tick_after: float = 0.0) -> None:
            nonlocal timestamp
            response_hex = respond_with_register_map(request_hex, registers)
            events.append(
                parse_modbus_interaction(
                    request_hex,
                    response_hex,
                    timestamp=timestamp,
                    metadata={"test": "cargo_sorting"},
                )
            )
            if tick_after:
                backend.tick(tick_after)
                timestamp += tick_after

        interact(_read(transaction_id=1, function_code=1, address=0, count=7))
        interact(_write_coil(transaction_id=2, address=0, energized=True))
        interact(_write_coil(transaction_id=3, address=1, energized=True))
        interact(_write_coil(transaction_id=4, address=6, energized=True), tick_after=2.0)
        interact(_read(transaction_id=5, function_code=2, address=24, count=1))
        interact(_write_coil(transaction_id=6, address=4, energized=True), tick_after=1.0)
        interact(_read(transaction_id=7, function_code=4, address=0, count=5), tick_after=3.0)
        interact(_read(transaction_id=8, function_code=4, address=0, count=5))

        model = learn_process_model(
            scenario,
            events,
            variables=plan.process_variables(),
        )

        self.assertEqual(model.observed_operation_counts["write_single_coil"], 4)
        self.assertGreaterEqual(len(model.response_examples), len(events))
        self.assertIn("turntable", model.variable_samples)
        self.assertGreaterEqual(
            max(sample.value for sample in model.variable_samples["box_position"]),
            50.0,
        )

        trace_backend = model.to_trace_backend(plan.process_variables())
        trace_backend.tick(2.0)
        snapshot = trace_backend.snapshot()
        self.assertIn("box_position", snapshot.measurements)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            write_modbus_trace_jsonl(path, events)
            reloaded = load_modbus_trace_jsonl(path)
        self.assertEqual(len(reloaded), len(events))
        self.assertEqual(reloaded[0].operation, "read_coils")


def _read(
    *,
    transaction_id: int,
    function_code: int,
    address: int,
    count: int,
) -> str:
    return build_modbus_tcp_read_request(
        transaction_id=transaction_id,
        unit_id=1,
        function_code=function_code,
        address=address,
        count=count,
    )


def _write_coil(
    *,
    transaction_id: int,
    address: int,
    energized: bool,
) -> str:
    return build_modbus_tcp_write_single_coil_request(
        transaction_id=transaction_id,
        unit_id=1,
        address=address,
        energized=energized,
    )


if __name__ == "__main__":
    unittest.main()
