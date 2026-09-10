from __future__ import annotations

from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.protocols.modbus import ModbusFrameError, parse_modbus_tcp_frame


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
        if context.transaction_id is not None:
            event_transaction_id = _parse_optional_u16(context.transaction_id)
            if (
                event_transaction_id is not None
                and frame.transaction_id != event_transaction_id
            ):
                raise ValueError("protocol reply transaction_id does not match request event")
        if context.unit_id is not None and frame.unit_id != context.unit_id:
            raise ValueError("protocol reply unit_id does not match request event")


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
