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


@dataclass(frozen=True, slots=True)
class ModbusTcpRequest:
    transaction_id: int
    protocol_id: int
    length: int
    unit_id: int
    function_code: int
    data: bytes
    raw: bytes
    address: int | None = None
    count: int | None = None
    values: tuple[int, ...] = ()


ALLOWED_NORMAL_FUNCTIONS = frozenset({1, 2, 3, 4, 5, 6, 15, 16})
ALLOWED_EXCEPTION_BASE_FUNCTIONS = frozenset({1, 2, 3, 4, 5, 6, 15, 16})
ALLOWED_EXCEPTION_CODES = frozenset(range(1, 12))
MAX_MODBUS_TCP_FRAME_BYTES = 260


def parse_modbus_tcp_request(payload: bytes | str) -> ModbusTcpRequest:
    """Parse the request-side Modbus TCP fields used for event normalization."""

    raw = _coerce_bytes(payload)
    transaction_id, protocol_id, length, unit_id, pdu = _parse_mbap(raw)
    function_code = pdu[0]
    data = pdu[1:]
    address: int | None = None
    count: int | None = None
    values: tuple[int, ...] = ()

    if function_code in {1, 2, 3, 4}:
        if len(data) != 4:
            raise ModbusFrameError("read request must contain address and count")
        address = int.from_bytes(data[0:2], "big")
        count = int.from_bytes(data[2:4], "big")
        if count <= 0:
            raise ModbusFrameError("read request count must be positive")
        if function_code in {1, 2} and count > 2000:
            raise ModbusFrameError("coil/discrete-input read count is too large")
        if function_code in {3, 4} and count > 125:
            raise ModbusFrameError("register read count is too large")
    elif function_code in {5, 6}:
        if len(data) != 4:
            raise ModbusFrameError("single-write request must contain address and value")
        address = int.from_bytes(data[0:2], "big")
        count = 1
        values = (int.from_bytes(data[2:4], "big"),)
    elif function_code in {15, 16}:
        if len(data) < 5:
            raise ModbusFrameError("multiple-write request is too short")
        address = int.from_bytes(data[0:2], "big")
        count = int.from_bytes(data[2:4], "big")
        byte_count = data[4]
        packed_values = data[5:]
        if byte_count != len(packed_values):
            raise ModbusFrameError("multiple-write byte count does not match data")
        if count <= 0:
            raise ModbusFrameError("multiple-write count must be positive")
        if function_code == 15:
            if byte_count != (count + 7) // 8:
                raise ModbusFrameError("multiple-coil byte count is invalid")
            values = _unpack_coils(packed_values, count)
        else:
            if byte_count != count * 2:
                raise ModbusFrameError("multiple-register byte count is invalid")
            if count > 123:
                raise ModbusFrameError("multiple-register write count is too large")
            values = tuple(
                int.from_bytes(packed_values[offset : offset + 2], "big")
                for offset in range(0, len(packed_values), 2)
            )
    else:
        raise ModbusFrameError(f"unsupported Modbus request function: {function_code}")

    return ModbusTcpRequest(
        transaction_id=transaction_id,
        protocol_id=protocol_id,
        length=length,
        unit_id=unit_id,
        function_code=function_code,
        data=data,
        raw=raw,
        address=address,
        count=count,
        values=values,
    )


def parse_modbus_tcp_frame(payload_hex: str) -> ModbusTcpFrame:
    """Parse and validate the generic Modbus TCP frame structure."""

    raw = _coerce_bytes(payload_hex)
    transaction_id, protocol_id, length, unit_id, pdu = _parse_mbap(raw)
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


def _coerce_bytes(payload: bytes | str) -> bytes:
    if isinstance(payload, bytes):
        return payload
    try:
        return bytes.fromhex(_clean_hex(payload))
    except ValueError as exc:
        raise ModbusFrameError("payload_hex must contain valid hex bytes") from exc


def _parse_mbap(raw: bytes) -> tuple[int, int, int, int, bytes]:
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
    return transaction_id, protocol_id, length, unit_id, pdu


def _unpack_coils(packed_values: bytes, count: int) -> tuple[int, ...]:
    values: list[int] = []
    for byte in packed_values:
        for bit in range(8):
            if len(values) == count:
                return tuple(values)
            values.append((byte >> bit) & 1)
    return tuple(values)
