from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from agentic_plc.processes import ProcessRegisterMap
from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    ModbusTcpRequest,
    build_modbus_tcp_exception_response,
    build_modbus_tcp_response_from_request,
    parse_modbus_tcp_frame,
    parse_modbus_tcp_request,
)
from agentic_plc.world.registers import RegisterAccessError, RegisterArea


READ_TABLE_BY_FUNCTION: dict[int, RegisterArea] = {
    1: RegisterArea.COILS,
    2: RegisterArea.DISCRETE_INPUTS,
    3: RegisterArea.HOLDING_REGISTERS,
    4: RegisterArea.INPUT_REGISTERS,
}

WRITE_TABLE_BY_FUNCTION: dict[int, RegisterArea] = {
    5: RegisterArea.COILS,
    6: RegisterArea.HOLDING_REGISTERS,
    15: RegisterArea.COILS,
    16: RegisterArea.HOLDING_REGISTERS,
}


@dataclass(frozen=True, slots=True)
class ModbusTraceEvent:
    """One request/response pair normalized from live TCP or pcap export."""

    timestamp: float
    request_hex: str
    response_hex: str
    function_code: int
    operation: str
    table: str
    address: int | None = None
    count: int | None = None
    request_values: tuple[int, ...] = ()
    response_values: tuple[int, ...] = ()
    exception_code: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_read(self) -> bool:
        return self.function_code in READ_TABLE_BY_FUNCTION

    @property
    def is_write(self) -> bool:
        return self.function_code in WRITE_TABLE_BY_FUNCTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "request_hex": self.request_hex,
            "response_hex": self.response_hex,
            "function_code": self.function_code,
            "operation": self.operation,
            "table": self.table,
            "address": self.address,
            "count": self.count,
            "request_values": list(self.request_values),
            "response_values": list(self.response_values),
            "exception_code": self.exception_code,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ModbusTraceEvent:
        return cls(
            timestamp=float(payload["timestamp"]),
            request_hex=str(payload["request_hex"]),
            response_hex=str(payload["response_hex"]),
            function_code=int(payload["function_code"]),
            operation=str(payload.get("operation", "")),
            table=str(payload.get("table", "")),
            address=_optional_int(payload.get("address")),
            count=_optional_int(payload.get("count")),
            request_values=tuple(int(item) for item in payload.get("request_values", ())),
            response_values=tuple(int(item) for item in payload.get("response_values", ())),
            exception_code=_optional_int(payload.get("exception_code")),
            metadata=dict(payload.get("metadata", {})),
        )


def parse_modbus_interaction(
    request_hex: str | bytes,
    response_hex: str | bytes,
    *,
    timestamp: float,
    metadata: Mapping[str, Any] | None = None,
) -> ModbusTraceEvent:
    """Parse one Modbus TCP request/response pair into acquisition trace form."""

    request = parse_modbus_tcp_request(request_hex)
    response = parse_modbus_tcp_frame(response_hex.hex() if isinstance(response_hex, bytes) else response_hex)
    operation = _operation_for_function(request.function_code)
    table = _table_for_function(request.function_code)
    exception_code = response.data[0] if response.is_exception and response.data else None
    response_values: tuple[int, ...] = ()
    if exception_code is None and request.function_code in READ_TABLE_BY_FUNCTION:
        response_values = _read_response_values(request, response.data)
    return ModbusTraceEvent(
        timestamp=float(timestamp),
        request_hex=_clean_hex(request.raw.hex()),
        response_hex=_clean_hex(response.raw.hex()),
        function_code=request.function_code,
        operation=operation,
        table=table.value,
        address=request.address,
        count=request.count,
        request_values=_normalize_request_values(request),
        response_values=response_values,
        exception_code=exception_code,
        metadata={
            **dict(metadata or {}),
            "request_transaction_id": request.transaction_id,
            "response_transaction_id": response.transaction_id,
            "unit_id": request.unit_id,
            "response_function_code": response.function_code,
            "response_is_exception": response.is_exception,
        },
    )


def respond_with_register_map(
    request_hex: str | bytes,
    register_map: ProcessRegisterMap,
    *,
    exception_on_error: bool = True,
) -> str:
    """Execute a Modbus request against a ProcessRegisterMap and build a reply.

    This helper is used by acquisition smoke tests and local synthetic traces.
    A real CODESYS/PLC acquisition run would produce equivalent pairs through
    a TCP client plus pcap/tshark capture.
    """

    request = parse_modbus_tcp_request(request_hex)
    try:
        if request.function_code in READ_TABLE_BY_FUNCTION:
            if request.address is None or request.count is None:
                raise RegisterAccessError("read request missing address/count")
            values = register_map.read(
                READ_TABLE_BY_FUNCTION[request.function_code],
                request.address,
                request.count,
            )
            return build_modbus_tcp_response_from_request(request, values=values)

        if request.function_code in {5, 6}:
            if request.address is None or not request.values:
                raise RegisterAccessError("single write request missing address/value")
            register_map.write(
                WRITE_TABLE_BY_FUNCTION[request.function_code],
                request.address,
                _decode_write_cell(request.function_code, request.values[0]),
            )
            return build_modbus_tcp_response_from_request(request)

        if request.function_code in {15, 16}:
            if request.address is None or request.count is None:
                raise RegisterAccessError("multi write request missing address/count")
            register_map.write_many(
                WRITE_TABLE_BY_FUNCTION[request.function_code],
                request.address,
                tuple(
                    _decode_write_cell(request.function_code, value)
                    for value in request.values
                ),
            )
            return build_modbus_tcp_response_from_request(request)
    except (RegisterAccessError, KeyError) as exc:
        if not exception_on_error:
            raise
        return build_modbus_tcp_exception_response(
            transaction_id=request.transaction_id,
            unit_id=request.unit_id,
            function_code=request.function_code,
            exception_code=2,
        )
    except (ValueError, ModbusFrameError) as exc:
        if not exception_on_error:
            raise
        return build_modbus_tcp_exception_response(
            transaction_id=request.transaction_id,
            unit_id=request.unit_id,
            function_code=request.function_code,
            exception_code=3,
        )

    raise ModbusFrameError(f"unsupported Modbus function: {request.function_code}")


def write_modbus_trace_jsonl(
    path: str | Path,
    events: Sequence[ModbusTraceEvent],
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(event.to_dict(), sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def load_modbus_trace_jsonl(path: str | Path) -> tuple[ModbusTraceEvent, ...]:
    events: list[ModbusTraceEvent] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Modbus trace JSON at line {line_number}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"Modbus trace line {line_number} must be an object")
        events.append(ModbusTraceEvent.from_dict(payload))
    return tuple(events)


def _read_response_values(request: ModbusTcpRequest, response_data: bytes) -> tuple[int, ...]:
    if not response_data:
        raise ModbusFrameError("read response has no byte count")
    byte_count = response_data[0]
    payload = response_data[1:]
    if byte_count != len(payload):
        raise ModbusFrameError("read response byte count mismatch")
    if request.function_code in {1, 2}:
        return _unpack_bits(payload, int(request.count or 0))
    if request.function_code in {3, 4}:
        if len(payload) % 2 != 0:
            raise ModbusFrameError("register response bytes must be even")
        values = tuple(
            int.from_bytes(payload[offset : offset + 2], "big")
            for offset in range(0, len(payload), 2)
        )
        if request.count is not None and len(values) != request.count:
            raise ModbusFrameError("register response count mismatch")
        return values
    raise ModbusFrameError("not a read response")


def _normalize_request_values(request: ModbusTcpRequest) -> tuple[int, ...]:
    if request.function_code in {5, 15}:
        return tuple(int(bool(_decode_write_cell(request.function_code, value))) for value in request.values)
    return tuple(int(value) for value in request.values)


def _decode_write_cell(function_code: int, value: int) -> int | bool:
    if function_code == 5:
        return int(value) == 0xFF00
    if function_code == 15:
        return bool(value)
    return int(value)


def _unpack_bits(payload: bytes, count: int) -> tuple[int, ...]:
    values: list[int] = []
    for byte in payload:
        for bit in range(8):
            if len(values) >= count:
                return tuple(values)
            values.append((byte >> bit) & 1)
    if len(values) != count:
        raise ModbusFrameError("bit response did not contain enough values")
    return tuple(values)


def _operation_for_function(function_code: int) -> str:
    return {
        1: "read_coils",
        2: "read_discrete_inputs",
        3: "read_holding_registers",
        4: "read_input_registers",
        5: "write_single_coil",
        6: "write_single_register",
        15: "write_multiple_coils",
        16: "write_multiple_registers",
    }.get(function_code, f"function_{function_code}")


def _table_for_function(function_code: int) -> RegisterArea:
    try:
        return READ_TABLE_BY_FUNCTION[function_code]
    except KeyError:
        return WRITE_TABLE_BY_FUNCTION[function_code]


def _clean_hex(value: str) -> str:
    return "".join(str(value).split()).lower()


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
