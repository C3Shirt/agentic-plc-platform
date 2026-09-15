from __future__ import annotations

from pathlib import Path
import unittest

from agentic_plc.agent import PhysicalProcessContext
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    FormulaEquation,
    FormulaExpressionError,
    FormulaProcessBackend,
    ProcessRegisterMap,
    ProcessVariable,
    ReadOnlyProcessVariable,
    ScenarioMapping,
)


class FormulaProcessBackendTests(unittest.TestCase):
    def test_formula_tick_updates_process_measurement_from_state(self) -> None:
        backend = _formula_tank_backend()

        backend.tick(1.0)

        snapshot = backend.snapshot()
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(snapshot.simulated_seconds, 1.0)
        self.assertAlmostEqual(snapshot.read("level_pct"), 53.9)
        self.assertEqual(snapshot.read("level_sp"), 70.0)

    def test_write_updates_writable_state_and_respects_bounds(self) -> None:
        backend = _formula_tank_backend()

        backend.write("level_sp", 80.0)
        backend.tick(1.0)

        self.assertEqual(backend.snapshot().revision, 2)
        self.assertEqual(backend.read("level_sp"), 80.0)
        self.assertAlmostEqual(backend.read("level_pct"), 55.9)

    def test_formula_outputs_are_clamped_to_declared_bounds(self) -> None:
        backend = FormulaProcessBackend(
            process_id="formula_tank_process",
            name="formula_tank",
            variables=[
                ProcessVariable(
                    variable_id="level_pct",
                    name="Level",
                    role="measurement",
                    minimum=0.0,
                    maximum=100.0,
                ),
            ],
            initial_state={"level_pct": 99.0},
            equations=[
                FormulaEquation(
                    target="level_pct",
                    expression="level_pct + 50 * dt",
                )
            ],
        )

        backend.tick(1.0)

        self.assertEqual(backend.read("level_pct"), 100.0)

    def test_rejects_write_to_read_only_measurement(self) -> None:
        backend = _formula_tank_backend()

        with self.assertRaises(ReadOnlyProcessVariable):
            backend.write("level_pct", 60.0)

    def test_rejects_unsafe_or_unknown_formula_names(self) -> None:
        with self.assertRaisesRegex(FormulaExpressionError, "allowed functions"):
            FormulaProcessBackend(
                process_id="bad_process",
                name="bad_formula",
                variables=[
                    ProcessVariable(
                        variable_id="level_pct",
                        name="Level",
                        role="measurement",
                    )
                ],
                initial_state={"level_pct": 1.0},
                equations=[
                    FormulaEquation(
                        target="level_pct",
                        expression="__import__('os')",
                    )
                ],
            )

    def test_modbus_mapping_reads_and_writes_formula_process(self) -> None:
        backend = _formula_tank_backend()
        scenario = ScenarioMapping.from_file(Path("scenarios/formula_tank/scenario.json"))
        register_map = ProcessRegisterMap(backend, scenario)
        context = PhysicalProcessContext(
            backend=backend,
            scenario=scenario,
            register_map=register_map,
        )

        self.assertEqual(register_map.read("holding_registers", 0), [700])
        self.assertEqual(register_map.read("input_registers", 0), [480])

        event = ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.WRITE_SETPOINT,
            operation="write_single_register",
            transaction_id="10",
            unit_id=1,
            address=0,
            count=1,
            requested_value=650,
            result="observed",
            metadata={"function_code": 6},
        )
        patch = context.world_patch_for_modbus_write_event(event)

        assert patch is not None
        self.assertEqual(patch.operations[0].path, "level_sp")
        self.assertEqual(patch.operations[0].value, 65.0)
        self.assertEqual(patch.metadata["process_id"], "formula_tank_process")
        self.assertEqual(patch.metadata["base_revision"], 0)


def _formula_tank_backend() -> FormulaProcessBackend:
    return FormulaProcessBackend(
        process_id="formula_tank_process",
        name="formula_tank",
        variables=[
            ProcessVariable(
                variable_id="level_pct",
                name="Tank level",
                role="measurement",
                unit="%",
                minimum=0.0,
                maximum=100.0,
            ),
            ProcessVariable(
                variable_id="level_sp",
                name="Level setpoint",
                role="setpoint",
                unit="%",
                minimum=0.0,
                maximum=100.0,
                writable=True,
            ),
            ProcessVariable(
                variable_id="pump_cmd",
                name="Pump command",
                role="manipulated_variable",
                minimum=0.0,
                maximum=1.0,
                writable=True,
            ),
        ],
        initial_state={
            "level_pct": 48.0,
            "level_sp": 70.0,
            "pump_cmd": 1.0,
        },
        equations=[
            FormulaEquation(
                target="level_pct",
                expression="level_pct + dt * (0.2 * (level_sp - level_pct) + 1.5 * pump_cmd)",
                description="First-order level response plus pump contribution.",
            )
        ],
        metadata={"simulator_family": "formula_tank"},
    )


if __name__ == "__main__":
    unittest.main()
