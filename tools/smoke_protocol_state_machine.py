from __future__ import annotations

import json

from agentic_plc.agent import (
    ModbusTcpStateMachine,
    ProtocolStateMachineRegistry,
)
from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.protocols.modbus import build_modbus_tcp_read_registers_response


def main() -> int:
    machine = ModbusTcpStateMachine()
    allowed, allowed_event = machine.observe(
        _event(
            intent=Intent.READ_PROCESS,
            operation="read_holding_registers",
            transaction_id="100",
            function_code=3,
            address=0,
            count=2,
        )
    )
    anomalous, anomalous_event = machine.observe(
        _event(
            intent=Intent.READ_PROCESS,
            operation="read_holding_registers",
            transaction_id="100",
            function_code=3,
            address=10,
            count=2,
        )
    )
    denied, denied_event = machine.observe(
        _event(
            intent=Intent.UNSUPPORTED_OPERATION,
            operation="function_99",
            transaction_id="101",
            function_code=99,
            address=0,
            count=1,
        )
    )

    generated_reply_blocked = False
    try:
        ProtocolReplyValidator().validate(
            ProtocolReply(
                protocol="modbus_tcp",
                payload_hex=build_modbus_tcp_read_registers_response(
                    transaction_id=101,
                    unit_id=1,
                    function_code=3,
                    values=[500],
                ),
                transaction_id="101",
                unit_id=1,
                reason="demo generated response",
            ),
            [denied_event],
        )
    except ValueError:
        generated_reply_blocked = True

    unknown_protocol, unknown_event = ProtocolStateMachineRegistry().observe(
        ICSEvent(
            protocol="iec104",
            session_id="iec-session",
            source_ip="127.0.0.1",
            actor_id="actor-iec",
            intent=Intent.READ_PROCESS,
            operation="interrogation",
        )
    )

    output = {
        "modbus_allowed": _summary(allowed, allowed_event),
        "modbus_anomalous": _summary(anomalous, anomalous_event),
        "modbus_denied": _summary(denied, denied_event),
        "generated_reply_blocked_for_denied_transition": generated_reply_blocked,
        "unknown_protocol_profile": _summary(unknown_protocol, unknown_event),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _summary(decision, event: ICSEvent) -> dict[str, object]:
    return {
        "allowed": decision.allowed,
        "status": decision.status.value,
        "reason": decision.reason,
        "metadata_status": event.metadata["protocol_fsm_status"],
        "metadata_allowed": event.metadata["protocol_fsm_allowed"],
    }


def _event(
    *,
    intent: Intent,
    operation: str,
    transaction_id: str,
    function_code: int,
    address: int,
    count: int,
) -> ICSEvent:
    return ICSEvent(
        protocol="modbus",
        session_id="modbus-smoke",
        source_ip="127.0.0.1",
        actor_id="actor-modbus",
        intent=intent,
        operation=operation,
        transaction_id=transaction_id,
        unit_id=1,
        address=address,
        count=count,
        result="observed",
        metadata={"function_code": function_code},
    )


if __name__ == "__main__":
    raise SystemExit(main())
