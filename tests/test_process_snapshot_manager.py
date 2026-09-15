from __future__ import annotations

import unittest

from agentic_plc.agent import AgentController, PhysicalProcessContext
from agentic_plc.contracts.actions import (
    AgentProposal,
    ProtocolReply,
    WorldPatch,
    WorldPatchOperation,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    ProcessSnapshotManager,
    ProcessVariable,
    TraceProcessBackend,
)
from agentic_plc.protocols.modbus import build_modbus_tcp_write_single_response
from agentic_plc.world.patch import WorldPatchError


class StaleWriteAckPlanner:
    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal:
        assert context is not None
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
                reason="acknowledge write from an outdated snapshot",
                metadata={"requires_accepted_world_patch": True},
            ),
            world_patch=WorldPatch(
                actor_id="actor-1",
                reason="stale write should not be accepted",
                operations=[
                    WorldPatchOperation(
                        path="valve_position",
                        value=42.0,
                        reason="decoded requested register value",
                    )
                ],
                metadata={
                    "base_revision": context.snapshot().revision - 1,
                    "process_id": context.process_id,
                    "backend_name": context.backend_name,
                },
            ),
        )


class ProcessSnapshotManagerTests(unittest.TestCase):
    def test_snapshot_card_preserves_revision_and_bounded_values(self) -> None:
        backend = _backend()
        snapshot = backend.snapshot()

        card = ProcessSnapshotManager.snapshot_card(snapshot, max_values_per_group=1)

        self.assertEqual(card["process_id"], "unit_process")
        self.assertEqual(card["backend_name"], "trace_process")
        self.assertEqual(card["revision"], 0)
        self.assertEqual(card["measurement_count"], 1)
        self.assertEqual(card["manipulated_variable_count"], 1)
        self.assertEqual(card["measurements"], {"tank_level": 50.0})
        self.assertEqual(card["manipulated_variables"], {"valve_position": 10.0})

    def test_applies_patch_and_records_authoritative_transition(self) -> None:
        backend = _backend()
        manager = ProcessSnapshotManager()
        base_revision = backend.snapshot().revision

        applied = manager.apply(
            backend,
            WorldPatch(
                actor_id="actor-1",
                reason="accepted control update",
                operations=[
                    WorldPatchOperation(
                        path="valve_position",
                        value=42.0,
                        reason="set attacker-facing valve output",
                    )
                ],
                metadata={
                    "base_revision": base_revision,
                    "process_id": "unit_process",
                    "backend_name": "trace_process",
                },
            ),
        )

        self.assertEqual(applied.before["revision"], base_revision)
        self.assertEqual(applied.after["revision"], base_revision + 1)
        self.assertEqual(backend.read("valve_position"), 42.0)
        self.assertEqual(len(manager.transitions), 1)
        self.assertEqual(
            manager.transitions[0].changed_variables,
            ("valve_position",),
        )
        self.assertEqual(manager.records[-1].reason, "accepted_world_patch")

    def test_rejects_stale_agent_patch(self) -> None:
        backend = _backend()
        manager = ProcessSnapshotManager()
        stale_revision = backend.snapshot().revision
        backend.tick(10.0)

        with self.assertRaisesRegex(WorldPatchError, "stale process patch"):
            manager.apply(
                backend,
                WorldPatch(
                    actor_id="actor-1",
                    reason="outdated LLM proposal",
                    operations=[
                        WorldPatchOperation(
                            path="valve_position",
                            value=50.0,
                        )
                    ],
                    metadata={"base_revision": stale_revision},
                ),
            )

        self.assertEqual(backend.read("valve_position"), 20.0)
        self.assertEqual(manager.transitions, ())

    def test_controller_withholds_write_ack_when_patch_is_stale(self) -> None:
        backend = _backend()
        context = _context(backend)
        backend.tick(10.0)
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
            count=1,
            requested_value=420,
            result="observed",
            metadata={"function_code": 6},
        )

        decision = AgentController(
            StaleWriteAckPlanner(),
            process_context=context,
        ).run_once([event])

        self.assertEqual(decision.protocol_replies, [])
        self.assertEqual(decision.world_patches, [])
        self.assertTrue(any(rejected.kind == "world_patch" for rejected in decision.rejected))
        self.assertTrue(any(rejected.kind == "protocol_reply" for rejected in decision.rejected))


def _backend() -> TraceProcessBackend:
    return TraceProcessBackend(
        process_id="unit_process",
        name="trace_process",
        time_seconds=[0.0, 10.0],
        variables=[
            ProcessVariable(
                variable_id="tank_level",
                name="Tank level",
                role="measurement",
                unit="%",
            ),
            ProcessVariable(
                variable_id="valve_position",
                name="Valve position",
                role="manipulated_variable",
                unit="%",
                minimum=0.0,
                maximum=100.0,
                writable=True,
            ),
        ],
        series={
            "tank_level": [50.0, 51.0],
            "valve_position": [10.0, 20.0],
        },
    )


def _context(backend: TraceProcessBackend) -> PhysicalProcessContext:
    from agentic_plc.processes import ProcessRegisterMap, ScenarioMapping

    scenario = ScenarioMapping.from_dict(
        {
            "scenario_id": "unit_process_slice",
            "process_id": "unit_process",
            "backend": {"type": "trace"},
            "plc_area": "unit_cell",
            "description": "test PLC slice",
            "points": [
                {
                    "variable_id": "valve_position",
                    "protocol": "modbus",
                    "table": "holding_registers",
                    "address": 5,
                    "access": "read_write",
                    "scale": 10.0,
                },
            ],
        }
    )
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=ProcessRegisterMap(backend, scenario),
    )


if __name__ == "__main__":
    unittest.main()
