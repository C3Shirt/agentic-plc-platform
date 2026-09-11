from __future__ import annotations

import json

from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_exception_response,
    build_modbus_tcp_read_bits_response,
    build_modbus_tcp_read_registers_response,
    build_modbus_tcp_response_from_request,
    build_modbus_tcp_write_multiple_response,
    build_modbus_tcp_write_single_response,
    parse_modbus_tcp_frame,
)


def main() -> int:
    validator = ProtocolReplyValidator()
    examples = [
        _validate(
            validator,
            name="read_coils",
            event=_event(1, transaction_id="17", address=0, count=9),
            payload_hex=build_modbus_tcp_read_bits_response(
                transaction_id=17,
                unit_id=1,
                function_code=1,
                values=[1, 0, 1, 1, 0, 0, 0, 1, 1],
            ),
        ),
        _validate(
            validator,
            name="read_holding_registers",
            event=_event(3, transaction_id="18", address=0, count=2),
            payload_hex=build_modbus_tcp_read_registers_response(
                transaction_id=18,
                unit_id=1,
                function_code=3,
                values=[500, 120],
            ),
        ),
        _validate(
            validator,
            name="write_single_register_ack",
            event=_event(6, transaction_id="19", address=5, requested_value=700),
            payload_hex=build_modbus_tcp_write_single_response(
                transaction_id=19,
                unit_id=1,
                function_code=6,
                address=5,
                value=700,
            ),
        ),
        _validate(
            validator,
            name="write_multiple_registers_ack",
            event=_event(16, transaction_id="20", address=5, count=2),
            payload_hex=build_modbus_tcp_write_multiple_response(
                transaction_id=20,
                unit_id=1,
                function_code=16,
                address=5,
                count=2,
            ),
        ),
        _validate(
            validator,
            name="exception_response",
            event=_event(3, transaction_id="21", address=99, count=1),
            payload_hex=build_modbus_tcp_exception_response(
                transaction_id=21,
                unit_id=1,
                function_code=3,
                exception_code=2,
            ),
        ),
        {
            "name": "from_raw_request",
            "payload_hex": build_modbus_tcp_response_from_request(
                bytes.fromhex("00 16 00 00 00 06 01 01 00 00 00 09"),
                values=[1, 0, 1, 1, 0, 0, 0, 1, 1],
            ),
        },
    ]
    print(json.dumps({"validated_modbus_responses": examples}, indent=2, sort_keys=True))
    return 0


def _validate(
    validator: ProtocolReplyValidator,
    *,
    name: str,
    event: ICSEvent,
    payload_hex: str,
) -> dict[str, object]:
    reply = ProtocolReply(
        protocol="modbus_tcp",
        transaction_id=event.transaction_id,
        unit_id=event.unit_id,
        payload_hex=payload_hex,
        reason=f"smoke {name}",
    )
    validator.validate(reply, [event])
    frame = parse_modbus_tcp_frame(payload_hex)
    return {
        "name": name,
        "transaction_id": frame.transaction_id,
        "unit_id": frame.unit_id,
        "function_code": frame.function_code,
        "is_exception": frame.is_exception,
        "data_hex": frame.data.hex(),
        "payload_hex": payload_hex,
    }


def _event(
    function_code: int,
    *,
    transaction_id: str,
    address: int,
    count: int | None = 1,
    requested_value: int | None = None,
) -> ICSEvent:
    intent = (
        Intent.READ_PROCESS
        if function_code in {1, 2, 3, 4}
        else Intent.WRITE_SETPOINT
    )
    operation = {
        1: "read_coils",
        2: "read_discrete_inputs",
        3: "read_holding_registers",
        4: "read_input_registers",
        5: "write_single_coil",
        6: "write_single_register",
        15: "write_multiple_coils",
        16: "write_multiple_registers",
    }[function_code]
    return ICSEvent(
        protocol="modbus",
        session_id="modbus-response-smoke",
        source_ip="127.0.0.1",
        actor_id="actor-local",
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
    raise SystemExit(main())
