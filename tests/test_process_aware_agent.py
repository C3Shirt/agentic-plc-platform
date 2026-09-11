from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentic_plc.agent import (
    AgentController,
    PhysicalProcessContext,
    RuleBasedDeceptionPlanner,
)
from agentic_plc.contracts.actions import (
    AgentProposal,
    ProtocolReply,
    WorldPatch,
    WorldPatchOperation,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    ProcessVariable,
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
    TraceProcessBackend,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_write_single_response,
    parse_modbus_tcp_frame,
)


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


class WrongWritePatchPlanner:
    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal:
        return AgentProposal(
            protocol_reply=ProtocolReply(
                protocol="modbus_tcp",
                payload_hex=build_modbus_tcp_write_single_response(
                    transaction_id=18,
                    unit_id=1,
                    function_code=6,
                    address=5,
                    value=420,
                ),
                transaction_id="18",
                unit_id=1,
                reason="acknowledge write with mismatched patch",
            ),
            world_patch=WorldPatch(
                actor_id="actor-1",
                reason="wrong process mutation",
                operations=[
                    WorldPatchOperation(
                        path="xmv_09",
                        value=42.0,
                        reason="does not correspond to the requested register",
                    )
                ],
            ),
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

    def test_process_context_builds_world_patch_for_mapped_modbus_write(self) -> None:
        context = _process_context()
        event = _te_write_event()

        patch = context.world_patch_for_modbus_write_event(event)

        assert patch is not None
        self.assertEqual(patch.operations[0].path, "xmv_10")
        self.assertEqual(patch.operations[0].value, 42.0)
        self.assertEqual(context.backend.read("xmv_10"), 10.0)

    def test_rule_planner_generates_write_ack_and_process_patch(self) -> None:
        context = _process_context()
        event = _te_write_event()

        decision = AgentController(
            RuleBasedDeceptionPlanner(),
            process_context=context,
        ).run_once([event])

        self.assertEqual(decision.rejected, [])
        self.assertEqual(len(decision.protocol_replies), 1)
        self.assertEqual(len(decision.world_patches), 1)
        frame = parse_modbus_tcp_frame(decision.protocol_replies[0].payload_hex)
        self.assertEqual(frame.function_code, 6)
        self.assertEqual(frame.data, bytes.fromhex("000501a4"))
        self.assertEqual(context.backend.read("xmv_10"), 42.0)

    def test_controller_withholds_write_ack_when_patch_does_not_match_write(
        self,
    ) -> None:
        context = _process_context()
        event = _te_write_event()

        decision = AgentController(
            WrongWritePatchPlanner(),
            process_context=context,
        ).run_once([event])

        self.assertEqual(decision.protocol_replies, [])
        self.assertEqual(decision.world_patches, [])
        self.assertEqual(context.backend.read("xmv_09"), 9.0)
        self.assertTrue(any(plan.kind == "world_patch" for plan in decision.rejected))
        self.assertTrue(any(plan.kind == "protocol_reply" for plan in decision.rejected))

    def test_rule_planner_generates_bit_read_reply_from_process_snapshot(self) -> None:
        context = _binary_process_context()
        event = ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.READ_PROCESS,
            operation="read_coils",
            transaction_id="19",
            unit_id=1,
            address=0,
            count=2,
            result="observed",
            metadata={"function_code": 1},
        )

        decision = AgentController(
            RuleBasedDeceptionPlanner(),
            process_context=context,
        ).run_once([event])

        self.assertEqual(decision.rejected, [])
        frame = parse_modbus_tcp_frame(decision.protocol_replies[0].payload_hex)
        self.assertEqual(frame.function_code, 1)
        self.assertEqual(frame.data, bytes.fromhex("01 01"))


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


def _binary_process_context() -> PhysicalProcessContext:
    backend = TraceProcessBackend(
        process_id="binary_process",
        name="binary_trace",
        time_seconds=[0.0],
        variables=[
            ProcessVariable(
                variable_id="pump_running",
                name="Pump running",
                role="measurement",
            ),
            ProcessVariable(
                variable_id="alarm_active",
                name="Alarm active",
                role="measurement",
            ),
        ],
        series={
            "pump_running": (1.0,),
            "alarm_active": (0.0,),
        },
    )
    scenario = ScenarioMapping.from_dict(
        {
            "scenario_id": "binary_plc_slice",
            "process_id": "binary_process",
            "backend": {"type": "trace"},
            "plc_area": "binary_cell",
            "description": "binary PLC slice for bit response tests",
            "points": [
                {
                    "variable_id": "pump_running",
                    "protocol": "modbus",
                    "table": "coils",
                    "address": 0,
                    "access": "read",
                    "data_type": "bool",
                },
                {
                    "variable_id": "alarm_active",
                    "protocol": "modbus",
                    "table": "coils",
                    "address": 1,
                    "access": "read",
                    "data_type": "bool",
                },
            ],
        }
    )
    register_map = ProcessRegisterMap(backend, scenario)
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )


def _te_write_event() -> ICSEvent:
    request_hex = _modbus_frame_hex(18, 1, bytes.fromhex("06 00 05 01 a4"))
    return ICSEvent(
        protocol="modbus",
        session_id="s1",
        source_ip="192.0.2.10",
        actor_id="actor-1",
        intent=Intent.CONTROL_OUTPUT,
        operation="write_single_register",
        transaction_id="18",
        unit_id=1,
        address=5,
        count=1,
        requested_value=420,
        result="observed",
        metadata={"function_code": 6, "request_hex": request_hex},
    )


def _modbus_frame_hex(transaction_id: int, unit_id: int, pdu: bytes) -> str:
    length = 1 + len(pdu)
    raw = bytearray()
    raw.extend(transaction_id.to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend(length.to_bytes(2, "big"))
    raw.append(unit_id)
    raw.extend(pdu)
    return bytes(raw).hex()


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
