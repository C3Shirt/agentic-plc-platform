from __future__ import annotations

import unittest

from agentic_plc.agent import (
    AgentRuntime,
    ModbusTcpStateMachine,
    ProtocolStateMachineRegistry,
    ProtocolTransitionStatus,
)
from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.protocols.modbus import build_modbus_tcp_read_registers_response
from agentic_plc.telemetry import InMemoryEventLog
from agentic_plc.world import TankPumpWorld


class ProtocolStateMachineTests(unittest.TestCase):
    def test_modbus_read_request_is_allowed_and_recorded(self) -> None:
        machine = ModbusTcpStateMachine()

        decision, enriched = machine.observe(
            _event(
                Intent.READ_PROCESS,
                operation="read_holding_registers",
                transaction_id="17",
                function_code=3,
                address=0,
                count=2,
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.status, ProtocolTransitionStatus.ALLOWED)
        self.assertEqual(enriched.metadata["protocol_fsm_allowed"], True)
        self.assertEqual(enriched.metadata["protocol_fsm_to_state"], "ready")
        state = machine.state_for("actor-1", "s1")
        assert state is not None
        self.assertEqual(state.event_count, 1)
        self.assertEqual(state.last_transaction_id, 17)

    def test_modbus_unsupported_function_is_denied(self) -> None:
        machine = ModbusTcpStateMachine()

        decision, enriched = machine.observe(
            _event(
                Intent.UNSUPPORTED_OPERATION,
                operation="function_99",
                transaction_id="18",
                function_code=99,
                address=0,
                count=1,
            )
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, ProtocolTransitionStatus.DENIED)
        self.assertEqual(
            enriched.metadata["protocol_fsm_reason"],
            "unsupported_modbus_function",
        )
        state = machine.state_for("actor-1", "s1")
        assert state is not None
        self.assertEqual(state.denied_count, 1)

    def test_modbus_transaction_reuse_with_different_request_is_anomalous(self) -> None:
        machine = ModbusTcpStateMachine()
        machine.observe(
            _event(
                Intent.READ_PROCESS,
                operation="read_holding_registers",
                transaction_id="19",
                function_code=3,
                address=0,
                count=1,
            )
        )

        decision, enriched = machine.observe(
            _event(
                Intent.READ_PROCESS,
                operation="read_holding_registers",
                transaction_id="19",
                function_code=3,
                address=10,
                count=1,
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.status, ProtocolTransitionStatus.ANOMALOUS)
        self.assertEqual(
            enriched.metadata["protocol_fsm_reason"],
            "duplicate_transaction_id_reused_with_different_request",
        )
        state = machine.state_for("actor-1", "s1")
        assert state is not None
        self.assertEqual(state.anomaly_count, 1)
        self.assertEqual(state.allowed_count, 2)

    def test_runtime_preserves_protocol_fsm_and_interaction_metadata(self) -> None:
        event_log = InMemoryEventLog()
        runtime = AgentRuntime(world=TankPumpWorld(), event_store=event_log)

        runtime.observe(
            _event(
                Intent.READ_PROCESS,
                operation="read_holding_registers",
                transaction_id="20",
                function_code=3,
                address=0,
                count=2,
            )
        )

        stored = event_log.list_events()[0]
        self.assertEqual(stored.metadata["protocol_fsm_allowed"], True)
        self.assertEqual(stored.metadata["interaction_phase"], "process_monitoring")
        state = runtime.protocol_session_state_for("actor-1", "modbus", "s1")
        assert state is not None
        self.assertEqual(state.phase.value, "ready")

    def test_generated_reply_is_rejected_when_protocol_fsm_denied_request(self) -> None:
        event = _event(
            Intent.UNSUPPORTED_OPERATION,
            operation="function_99",
            transaction_id="21",
            function_code=99,
            address=0,
            count=1,
        )
        _, enriched = ModbusTcpStateMachine().observe(event)
        reply = ProtocolReply(
            protocol="modbus_tcp",
            payload_hex=build_modbus_tcp_read_registers_response(
                transaction_id=21,
                unit_id=1,
                function_code=3,
                values=[100],
            ),
            transaction_id="21",
            unit_id=1,
            reason="should not pass because the request state was denied",
        )

        with self.assertRaisesRegex(ValueError, "protocol state machine"):
            ProtocolReplyValidator().validate(reply, [enriched])

    def test_registry_allows_unknown_protocol_without_profile(self) -> None:
        registry = ProtocolStateMachineRegistry()

        decision, enriched = registry.observe(
            ICSEvent(
                protocol="iec104",
                session_id="iec-session",
                source_ip="192.0.2.10",
                actor_id="actor-1",
                intent=Intent.READ_PROCESS,
                operation="interrogation",
            )
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.status, ProtocolTransitionStatus.NO_PROFILE)
        self.assertEqual(enriched.metadata["protocol_fsm_status"], "no_profile")


def _event(
    intent: Intent,
    *,
    operation: str,
    transaction_id: str,
    function_code: int,
    address: int | None,
    count: int | None,
    requested_value: object | None = None,
) -> ICSEvent:
    return ICSEvent(
        protocol="modbus",
        session_id="s1",
        source_ip="192.0.2.10",
        actor_id="actor-1",
        intent=intent,
        operation=operation,
        transaction_id=transaction_id,
        unit_id=1,
        address=address,
        count=count,
        requested_value=requested_value,
        result="observed",
        metadata={"function_code": function_code},
    )


if __name__ == "__main__":
    unittest.main()
