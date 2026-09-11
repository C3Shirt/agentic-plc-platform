from __future__ import annotations

from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    ModbusTcpFrame,
    parse_modbus_tcp_frame,
)


class ProtocolReplyValidator:
    """Validates generated protocol bytes before an adapter can send them."""

    SUPPORTED_PROTOCOLS = frozenset({"modbus_tcp", "modbus"})

    def validate(
        self,
        reply: ProtocolReply,
        events: list[ICSEvent] | None = None,
    ) -> None:
        protocol = reply.protocol.lower()
        if protocol not in self.SUPPORTED_PROTOCOLS:
            raise ValueError(f"unsupported generated protocol reply: {reply.protocol}")
        if not reply.reason.strip():
            raise ValueError("protocol reply reason is required")

        try:
            frame = parse_modbus_tcp_frame(reply.payload_hex)
        except ModbusFrameError as exc:
            raise ValueError(str(exc)) from exc

        expected_transaction_id = _parse_optional_u16(reply.transaction_id)
        if expected_transaction_id is not None and frame.transaction_id != expected_transaction_id:
            raise ValueError("protocol reply transaction_id does not match metadata")
        if reply.unit_id is not None and frame.unit_id != int(reply.unit_id):
            raise ValueError("protocol reply unit_id does not match metadata")

        context = _latest_event_with_protocol(events or [], {"modbus", "modbus_tcp"})
        if context is None:
            return
        if context.metadata.get("protocol_fsm_allowed") is False:
            reason = context.metadata.get(
                "protocol_fsm_reason",
                "protocol_state_denied",
            )
            raise ValueError(
                f"protocol reply blocked by protocol state machine: {reason}"
            )
        if context.transaction_id is not None:
            event_transaction_id = _parse_optional_u16(context.transaction_id)
            if (
                event_transaction_id is not None
                and frame.transaction_id != event_transaction_id
            ):
                raise ValueError("protocol reply transaction_id does not match request event")
        if context.unit_id is not None and frame.unit_id != context.unit_id:
            raise ValueError("protocol reply unit_id does not match request event")
        _validate_modbus_response_matches_event(frame, context)


def _parse_optional_u16(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = int(text, 16) if text.lower().startswith("0x") else int(text)
    if not 0 <= parsed <= 65535:
        raise ValueError("transaction_id must fit in uint16")
    return parsed


def _latest_event_with_protocol(
    events: list[ICSEvent],
    protocols: set[str],
) -> ICSEvent | None:
    for event in reversed(events):
        if event.protocol.lower() in protocols:
            return event
    return None


def _validate_modbus_response_matches_event(
    frame: ModbusTcpFrame,
    event: ICSEvent,
) -> None:
    expected_function = _expected_modbus_function(event)
    if expected_function is None:
        return

    if frame.is_exception:
        if frame.function_code & 0x7F != expected_function:
            raise ValueError("protocol reply exception function_code does not match request")
        return

    if frame.function_code != expected_function:
        raise ValueError("protocol reply function_code does not match request")

    if expected_function in {1, 2}:
        _validate_read_bits_response_shape(frame, event)
        return
    if expected_function in {3, 4}:
        _validate_read_registers_response_shape(frame, event)
        return
    if expected_function in {5, 6}:
        _validate_write_single_response_echo(frame, event)
        return
    if expected_function in {15, 16}:
        _validate_write_multiple_response_echo(frame, event)


def _expected_modbus_function(event: ICSEvent) -> int | None:
    function_code = event.metadata.get("function_code")
    if function_code is not None:
        return int(function_code)
    return {
        "read_coils": 1,
        "read_discrete_inputs": 2,
        "read_holding_registers": 3,
        "read_input_registers": 4,
        "write_single_coil": 5,
        "write_single_register": 6,
        "write_multiple_coils": 15,
        "write_multiple_registers": 16,
    }.get(event.operation)


def _validate_read_bits_response_shape(
    frame: ModbusTcpFrame,
    event: ICSEvent,
) -> None:
    if event.count is None:
        return
    expected_byte_count = (int(event.count) + 7) // 8
    actual_byte_count = frame.data[0]
    if actual_byte_count != expected_byte_count:
        raise ValueError("protocol reply bit-read byte count does not match request")


def _validate_read_registers_response_shape(
    frame: ModbusTcpFrame,
    event: ICSEvent,
) -> None:
    if event.count is None:
        return
    expected_byte_count = int(event.count) * 2
    actual_byte_count = frame.data[0]
    if actual_byte_count != expected_byte_count:
        raise ValueError("protocol reply register-read byte count does not match request")


def _validate_write_single_response_echo(
    frame: ModbusTcpFrame,
    event: ICSEvent,
) -> None:
    address, value = _parse_write_echo(frame)
    if event.address is not None and address != int(event.address):
        raise ValueError("protocol reply write address does not match request")
    expected_value = _expected_single_write_value(event)
    if expected_value is not None and value != expected_value:
        raise ValueError("protocol reply write value does not match request")


def _validate_write_multiple_response_echo(
    frame: ModbusTcpFrame,
    event: ICSEvent,
) -> None:
    address, count = _parse_write_echo(frame)
    if event.address is not None and address != int(event.address):
        raise ValueError("protocol reply write address does not match request")
    if event.count is not None and count != int(event.count):
        raise ValueError("protocol reply write count does not match request")


def _parse_write_echo(frame: ModbusTcpFrame) -> tuple[int, int]:
    if len(frame.data) != 4:
        raise ValueError("protocol reply write echo must contain address and value/count")
    return (
        int.from_bytes(frame.data[0:2], "big"),
        int.from_bytes(frame.data[2:4], "big"),
    )


def _expected_single_write_value(event: ICSEvent) -> int | None:
    if event.requested_value is None:
        return None
    function_code = _expected_modbus_function(event)
    value = event.requested_value
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if isinstance(value, tuple):
        if not value:
            return None
        value = value[0]
    if function_code == 5:
        if isinstance(value, bool):
            return 0xFF00 if value else 0x0000
        numeric = int(value)
        if numeric in {0, 1}:
            return 0xFF00 if numeric else 0x0000
        return numeric
    return int(value)
