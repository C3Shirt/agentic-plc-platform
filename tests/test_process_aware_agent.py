from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentic_plc.agent import (
    AgentController,
    PhysicalProcessContext,
    RuleBasedDeceptionPlanner,
)
from agentic_plc.contracts.actions import AgentProposal, WorldPatch, WorldPatchOperation
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)
from agentic_plc.protocols.modbus import parse_modbus_tcp_frame


class ProcessPatchPlanner:
    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal:
        assert context is not None
        return AgentProposal(
            world_patch=WorldPatch(
                actor_id="actor-1",
                reason="bounded process-state adaptation",
                operations=[
                    WorldPatchOperation(
                        path=context.writable_variable_ids()[0],
                        value=42.0,
                        reason="hold attacker-facing control output at a plausible value",
                    )
                ],
            )
        )


class ProcessAwareAgentTests(unittest.TestCase):
    def test_process_context_describes_current_backend_and_plc_slice(self) -> None:
        context = _process_context()

        prompt = context.to_prompt_dict()

        self.assertEqual(prompt["backend_name"], "tennessee_eastman_trace")
        self.assertEqual(
            prompt["scenario_id"], "tennessee_eastman_reactor_separator_cell"
        )
        self.assertIn("xmv_10", prompt["writable_variable_ids"])
        self.assertGreaterEqual(len(prompt["exposed_points"]), 10)

    def test_rule_planner_generates_read_reply_from_process_snapshot(self) -> None:
        context = _process_context()
        event = ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.READ_PROCESS,
            operation="read_input_registers",
            transaction_id="17",
            unit_id=1,
            address=0,
            count=3,
            result="observed",
            metadata={"function_code": 4},
        )

        decision = AgentController(
            RuleBasedDeceptionPlanner(),
            process_context=context,
        ).run_once([event])

        self.assertEqual(decision.rejected, [])
        frame = parse_modbus_tcp_frame(decision.protocol_replies[0].payload_hex)
        self.assertEqual(frame.function_code, 4)
        self.assertEqual(frame.data, bytes.fromhex("0600070050005a"))

    def test_process_patch_applies_to_writable_backend_variable(self) -> None:
        context = _process_context()
        event = ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.CONTROL_OUTPUT,
            operation="write_single_register",
            transaction_id="18",
            unit_id=1,
            address=5,
            requested_value=420,
            result="observed",
            metadata={"function_code": 6},
        )

        decision = AgentController(
            ProcessPatchPlanner(),
            process_context=context,
        ).run_once([event])

        self.assertEqual(decision.rejected, [])
        self.assertEqual(len(decision.world_patches), 1)
        patched_path = decision.world_patches[0].patch.operations[0].path
        self.assertEqual(context.backend.read(patched_path), 42.0)


def _process_context() -> PhysicalProcessContext:
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
    register_map = ProcessRegisterMap(backend, scenario)
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
