import unittest

from agentic_plc.agent import (
    AgentController,
    RuleBasedDeceptionPlanner,
    proposal_from_payload,
)
from agentic_plc.contracts.actions import (
    AgentProposal,
    ProtocolReply,
    WorldPatch,
    WorldPatchOperation,
)
from agentic_plc.contracts.events import DeceptionPlan, ICSEvent, Intent
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_read_registers_response,
    build_modbus_tcp_write_single_response,
)
from agentic_plc.world import TankPumpWorld


class BadPlanner:
    def propose(self, events: list[ICSEvent]) -> DeceptionPlan:
        return DeceptionPlan(
            actor_id="actor-1",
            action="write_arbitrary_register",
            target="holding:0",
            reason="invalid direct mutation",
        )


class ProtocolReplyPlanner:
    def propose(self, events: list[ICSEvent]) -> AgentProposal:
        return AgentProposal(
            protocol_reply=ProtocolReply(
                protocol="modbus_tcp",
                payload_hex=build_modbus_tcp_read_registers_response(
                    transaction_id=17,
                    unit_id=1,
                    function_code=3,
                    values=[500, 120],
                ),
                transaction_id="17",
                unit_id=1,
                reason="generated read response",
            )
        )


class WriteAckOnlyPlanner:
    def propose(self, events: list[ICSEvent]) -> AgentProposal:
        return AgentProposal(
            protocol_reply=ProtocolReply(
                protocol="modbus_tcp",
                payload_hex=build_modbus_tcp_write_single_response(
                    transaction_id=18,
                    unit_id=1,
                    function_code=6,
                    address=5,
                    value=700,
                ),
                transaction_id="18",
                unit_id=1,
                reason="write ack without state mutation",
            )
        )


class BadWorldPatchPlanner:
    def propose(self, events: list[ICSEvent]) -> AgentProposal:
        return AgentProposal(
            world_patch=WorldPatch(
                actor_id="actor-1",
                reason="bad patch",
                operations=[
                    WorldPatchOperation(
                        path="level_percent",
                        value=101.0,
                        reason="outside physical range",
                    )
                ],
            )
        )


class AgentControllerTests(unittest.TestCase):
    def test_rule_planner_accepts_repeated_setpoint_plan(self) -> None:
        world = TankPumpWorld()
        events = [
            self._event(Intent.WRITE_SETPOINT, 700),
            self._event(Intent.WRITE_SETPOINT, 720),
        ]

        decision = AgentController(RuleBasedDeceptionPlanner(), world=world).run_once(
            events
        )

        self.assertTrue(decision.has_action)
        self.assertEqual(decision.accepted[0].action, "publish_maintenance_note")
        self.assertEqual(decision.accepted[0].target, "WO-2048")
        self.assertEqual(world.state.level_percent, 58.5)
        self.assertEqual(len(decision.world_patches), 1)

    def test_controller_rejects_unvalidated_planner_output(self) -> None:
        decision = AgentController(BadPlanner()).run_once([self._event(Intent.DISCOVER)])

        self.assertFalse(decision.has_action)
        self.assertEqual(len(decision.rejected), 1)
        self.assertIn("not allowed", decision.rejected[0].error)

    def test_controller_allows_no_action(self) -> None:
        decision = AgentController(RuleBasedDeceptionPlanner()).run_once(
            [self._event(Intent.READ_PROCESS)]
        )

        self.assertFalse(decision.has_action)
        self.assertEqual(decision.rejected, [])

    def test_controller_accepts_generated_protocol_reply(self) -> None:
        decision = AgentController(ProtocolReplyPlanner()).run_once(
            [self._read_event(transaction_id="17", unit_id=1)]
        )

        self.assertTrue(decision.has_action)
        self.assertEqual(len(decision.protocol_replies), 1)
        self.assertEqual(decision.rejected, [])

    def test_controller_rejects_protocol_reply_transaction_mismatch(self) -> None:
        decision = AgentController(ProtocolReplyPlanner()).run_once(
            [self._read_event(transaction_id="18", unit_id=1)]
        )

        self.assertFalse(decision.has_action)
        self.assertEqual(decision.rejected[0].kind, "protocol_reply")
        self.assertIn("transaction_id", decision.rejected[0].error)

    def test_controller_withholds_generated_write_ack_without_world_patch(self) -> None:
        decision = AgentController(WriteAckOnlyPlanner()).run_once(
            [self._write_event(transaction_id="18", unit_id=1)]
        )

        self.assertFalse(decision.has_action)
        self.assertEqual(decision.protocol_replies, [])
        self.assertEqual(decision.rejected[0].kind, "protocol_reply")
        self.assertIn("world patch", decision.rejected[0].error)

    def test_controller_rejects_invalid_world_patch(self) -> None:
        decision = AgentController(BadWorldPatchPlanner(), world=TankPumpWorld()).run_once(
            [self._event(Intent.READ_PROCESS)]
        )

        self.assertFalse(decision.has_action)
        self.assertEqual(decision.rejected[0].kind, "world_patch")
        self.assertIn("level_percent", decision.rejected[0].error)

    def test_parses_llm_action_envelope(self) -> None:
        proposal = proposal_from_payload(
            {
                "deception_plan": {
                    "actor_id": "actor-1",
                    "action": "publish_maintenance_note",
                    "target": "WO-2048",
                    "reason": "trajectory",
                },
                "protocol_reply": {
                    "protocol": "modbus_tcp",
                    "payload_hex": build_modbus_tcp_read_registers_response(
                        transaction_id=17,
                        unit_id=1,
                        function_code=3,
                        values=[500],
                    ),
                    "reason": "generated response",
                    "transaction_id": "17",
                    "unit_id": 1,
                },
                "world_patch": {
                    "actor_id": "actor-1",
                    "reason": "narrative state",
                    "operations": [
                        {
                            "path": "pressure_bar",
                            "value": 1.3,
                            "reason": "small drift",
                        }
                    ],
                },
            }
        )

        self.assertIsNotNone(proposal)
        self.assertIsNotNone(proposal.deception_plan)
        self.assertIsNotNone(proposal.protocol_reply)
        self.assertIsNotNone(proposal.world_patch)

    def _event(self, intent: Intent, requested_value: int | None = None) -> ICSEvent:
        return ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=intent,
            operation=intent.value,
            requested_value=requested_value,
            result="accepted",
            world_revision=1,
        )

    def _write_event(self, transaction_id: str, unit_id: int) -> ICSEvent:
        return ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.CONTROL_OUTPUT,
            operation="write_single_register",
            transaction_id=transaction_id,
            unit_id=unit_id,
            address=5,
            count=1,
            requested_value=700,
            result="observed",
            world_revision=1,
            metadata={"function_code": 6},
        )

    def _read_event(self, transaction_id: str, unit_id: int) -> ICSEvent:
        return ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.READ_PROCESS,
            operation="read_holding_registers",
            transaction_id=transaction_id,
            unit_id=unit_id,
            address=0,
            count=2,
            result="observed",
            world_revision=1,
        )


if __name__ == "__main__":
    unittest.main()
