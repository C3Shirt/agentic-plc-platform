from __future__ import annotations

import json

from agentic_plc.agent import AgentRuntime, InteractionPhase
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.telemetry import InMemoryEventLog
from agentic_plc.world import TankPumpWorld


def main() -> int:
    event_log = InMemoryEventLog()
    runtime = AgentRuntime(world=TankPumpWorld(), event_store=event_log)

    events = [
        _read(address=0, transaction_id=10),
        _read(address=1, transaction_id=11),
        _read(address=2, transaction_id=12),
        _read(address=3, transaction_id=13),
        _read(address=4, transaction_id=14),
        _write(address=0, value=700, transaction_id=15),
        _read(address=0, transaction_id=16),
    ]

    decisions = []
    for event in events:
        decision = runtime.observe_and_decide(event)
        stored_event = event_log.list_events()[-1]
        decisions.append(
            {
                "operation": stored_event.operation,
                "address": stored_event.address,
                "phase": stored_event.metadata["interaction_phase"],
                "phase_reason": stored_event.metadata["interaction_phase_reason"],
                "accepted_actions": [plan.action for plan in decision.accepted],
                "protocol_reply_count": len(decision.protocol_replies),
            }
        )

    state = runtime.protocol_state_for("actor-1", "modbus")
    output = {
        "observed_phases": decisions,
        "final_actor_protocol_state": (
            state.to_prompt_dict() if state is not None else None
        ),
        "expected_terminal_phase": InteractionPhase.EFFECT_VERIFICATION.value,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _read(address: int, transaction_id: int) -> ICSEvent:
    return ICSEvent(
        protocol="modbus",
        session_id="protocol-smoke",
        source_ip="127.0.0.1",
        actor_id="actor-1",
        intent=Intent.READ_PROCESS,
        operation="read_holding_registers",
        transaction_id=str(transaction_id),
        unit_id=1,
        address=address,
        count=1,
        result="observed",
        metadata={"function_code": 3},
    )


def _write(address: int, value: int, transaction_id: int) -> ICSEvent:
    return ICSEvent(
        protocol="modbus",
        session_id="protocol-smoke",
        source_ip="127.0.0.1",
        actor_id="actor-1",
        intent=Intent.WRITE_SETPOINT,
        operation="write_single_register",
        transaction_id=str(transaction_id),
        unit_id=1,
        address=address,
        count=1,
        requested_value=value,
        result="observed",
        metadata={"function_code": 6},
    )


if __name__ == "__main__":
    raise SystemExit(main())
