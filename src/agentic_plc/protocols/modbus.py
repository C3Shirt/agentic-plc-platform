from __future__ import annotations

from dataclasses import dataclass


class ModbusFrameError(ValueError):
    """Raised when a generated Modbus TCP frame is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class ModbusTcpFrame:
    transaction_id: int
    protocol_id: int
    length: int
    unit_id: int
    function_code: int
    data: bytes
    raw: bytes

    @property
    def is_exception(self) -> bool:
        return self.function_code >= 0x80


ALLOWED_NORMAL_FUNCTIONS = frozenset({1, 2, 3, 4, 5, 6, 15, 16})
ALLOWED_EXCEPTION_BASE_FUNCTIONS = frozenset({1, 2, 3, 4, 5, 6, 15, 16})
ALLOWED_EXCEPTION_CODES = frozenset(range(1, 12))
MAX_MODBUS_TCP_FRAME_BYTES = 260


def parse_modbus_tcp_frame(payload_hex: str) -> ModbusTcpFrame:
    """Parse and validate the generic Modbus TCP frame structure."""

    try:
        raw = bytes.fromhex(_clean_hex(payload_hex))
    except ValueError as exc:
        raise ModbusFrameError("payload_hex must contain valid hex bytes") from exc

    if len(raw) < 8:
        raise ModbusFrameError("Modbus TCP frame is too short")
    if len(raw) > MAX_MODBUS_TCP_FRAME_BYTES:
        raise ModbusFrameError("Modbus TCP frame is too long")

    transaction_id = int.from_bytes(raw[0:2], "big")
    protocol_id = int.from_bytes(raw[2:4], "big")
    length = int.from_bytes(raw[4:6], "big")
    unit_id = raw[6]
    pdu = raw[7:]

    if protocol_id != 0:
        raise ModbusFrameError("Modbus TCP protocol id must be zero")
    if length != len(raw) - 6:
        raise ModbusFrameError("Modbus TCP length field does not match payload size")
    if not pdu:
        raise ModbusFrameError("Modbus TCP PDU is empty")
    if not 0 <= unit_id <= 247:
        raise ModbusFrameError("unit_id must be between 0 and 247")

    function_code = pdu[0]
    data = pdu[1:]
    _validate_function(function_code, data)
    return ModbusTcpFrame(
        transaction_id=transaction_id,
        protocol_id=protocol_id,
        length=length,
        unit_id=unit_id,
        function_code=function_code,
        data=data,
        raw=raw,
    )


def build_modbus_tcp_read_registers_response(
    transaction_id: int,
    unit_id: int,
    function_code: int,
    values: list[int],
) -> str:
    if function_code not in {3, 4}:
        raise ModbusFrameError("read-register response function_code must be 3 or 4")
    if not values:
        raise ModbusFrameError("read-register response requires at least one value")
    if len(values) > 125:
        raise ModbusFrameError("read-register response supports at most 125 registers")
    data = bytearray([function_code, len(values) * 2])
    for value in values:
        if not 0 <= int(value) <= 65535:
            raise ModbusFrameError("register value must fit in uint16")
        data.extend(int(value).to_bytes(2, "big"))
    return _build_frame(transaction_id=transaction_id, unit_id=unit_id, pdu=bytes(data))


def build_modbus_tcp_exception_response(
    transaction_id: int,
    unit_id: int,
    function_code: int,
    exception_code: int,
) -> str:
    if function_code not in ALLOWED_EXCEPTION_BASE_FUNCTIONS:
        raise ModbusFrameError("unsupported base function for exception response")
    if exception_code not in ALLOWED_EXCEPTION_CODES:
        raise ModbusFrameError("unsupported Modbus exception code")
    return _build_frame(
        transaction_id=transaction_id,
        unit_id=unit_id,
        pdu=bytes([function_code | 0x80, exception_code]),
    )


def _validate_function(function_code: int, data: bytes) -> None:
    if function_code >= 0x80:
        base_function = function_code & 0x7F
        if base_function not in ALLOWED_EXCEPTION_BASE_FUNCTIONS:
            raise ModbusFrameError("unsupported Modbus exception base function")
        if len(data) != 1:
            raise ModbusFrameError("Modbus exception response must have one data byte")
        if data[0] not in ALLOWED_EXCEPTION_CODES:
            raise ModbusFrameError("unsupported Modbus exception code")
        return

    if function_code not in ALLOWED_NORMAL_FUNCTIONS:
        raise ModbusFrameError(f"unsupported Modbus function code: {function_code}")

    if function_code in {1, 2, 3, 4}:
        if not data:
            raise ModbusFrameError("read response must include a byte count")
        byte_count = data[0]
        if byte_count != len(data) - 1:
            raise ModbusFrameError("read response byte count does not match data")
        if function_code in {3, 4} and byte_count % 2 != 0:
            raise ModbusFrameError("register read response byte count must be even")
        return

    if function_code in {5, 6, 15, 16} and len(data) != 4:
        raise ModbusFrameError("write response must echo address and value/count")


def _build_frame(transaction_id: int, unit_id: int, pdu: bytes) -> str:
    if not 0 <= int(transaction_id) <= 65535:
        raise ModbusFrameError("transaction_id must fit in uint16")
    if not 0 <= int(unit_id) <= 247:
        raise ModbusFrameError("unit_id must be between 0 and 247")
    length = 1 + len(pdu)
    if length > 254:
        raise ModbusFrameError("Modbus TCP PDU is too large")
    raw = bytearray()
    raw.extend(int(transaction_id).to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend(length.to_bytes(2, "big"))
    raw.append(int(unit_id))
    raw.extend(pdu)
    return raw.hex()


def _clean_hex(payload_hex: str) -> str:
    return "".join(str(payload_hex).split())
