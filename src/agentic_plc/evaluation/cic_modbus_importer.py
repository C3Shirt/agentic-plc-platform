from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from agentic_plc.adapters import ModbusHookContext, event_from_modbus_tcp_request
from agentic_plc.agent.protocol_state_machine import ModbusTcpStateMachine
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.evaluation.consistency_benchmark import BenchmarkCase, BenchmarkStep
from agentic_plc.protocols.modbus import ModbusFrameError, parse_modbus_tcp_request


@dataclass(frozen=True, slots=True)
class CICModbusImportOptions:
    """Controls how public CIC/tshark Modbus CSV rows become benchmark steps."""

    case_id: str = "cic_modbus_import"
    description: str | None = None
    session_id: str = "cic-modbus-session"
    default_source_ip: str = "0.0.0.0"
    default_destination_ip: str = "0.0.0.0"
    default_unit_id: int = 1
    default_source_port: int | None = None
    default_destination_port: int | None = 502
    actor_id_prefix: str = "cic-actor"
    max_rows: int | None = None
    include_responses: bool = False
    requires_process_context: bool = False


@dataclass(frozen=True, slots=True)
class CICAttackLabel:
    """One CIC Modbus 2023 attack-log row."""

    timestamp: str | None
    target_ip: str | None
    attack: str | None
    transaction_id: str | None
    row_number: int

    def metadata(self) -> dict[str, object]:
        data: dict[str, object] = {
            "cic_attack_log_row": self.row_number,
        }
        if self.timestamp:
            data["cic_attack_timestamp"] = self.timestamp
        if self.target_ip:
            data["cic_attack_target_ip"] = self.target_ip
        if self.attack:
            data["cic_attack"] = self.attack
            data["cic_attack_label"] = self.attack
        if self.transaction_id:
            data["cic_attack_transaction_id"] = self.transaction_id
        return data


class CICAttackLogIndex:
    """Lookup CIC attack labels by transaction id, using target IP to disambiguate."""

    def __init__(self, labels: Iterable[CICAttackLabel] = ()) -> None:
        self._by_transaction_id: dict[str, list[CICAttackLabel]] = {}
        for label in labels:
            if label.transaction_id:
                key = _transaction_id_key(label.transaction_id)
                self._by_transaction_id.setdefault(key, []).append(label)

    @classmethod
    def from_csv(cls, path: Path | str) -> CICAttackLogIndex:
        labels: list[CICAttackLabel] = []
        for row_number, row in _iter_csv_rows(path):
            transaction_id = _first_value(row, _TRANSACTION_ID_ALIASES)
            labels.append(
                CICAttackLabel(
                    timestamp=_first_value(row, _TIMESTAMP_ALIASES),
                    target_ip=_first_value(row, _TARGET_IP_ALIASES),
                    attack=_first_value(row, _ATTACK_ALIASES),
                    transaction_id=transaction_id,
                    row_number=row_number,
                )
            )
        return cls(labels)

    def match(
        self,
        *,
        transaction_id: str | int | None,
        destination_ip: str | None,
    ) -> CICAttackLabel | None:
        if transaction_id is not None:
            matches = self._by_transaction_id.get(_transaction_id_key(transaction_id))
            if matches:
                if destination_ip:
                    target_matches = [
                        label
                        for label in matches
                        if label.target_ip == destination_ip.strip()
                    ]
                    if target_matches:
                        return target_matches[0]
                return matches[0]
        return None

    @property
    def empty(self) -> bool:
        return not self._by_transaction_id


def import_cic_modbus_benchmark(
    packet_csv: Path | str,
    *,
    attack_log_csv: Path | str | None = None,
    options: CICModbusImportOptions | None = None,
) -> BenchmarkCase:
    """Import CIC Modbus/tshark CSV rows into the local benchmark schema.

    The importer deliberately consumes CSV rows rather than raw PCAP files. Raw
    captures can be converted with tshark/scapy/Zeek outside the library, while
    the benchmark layer remains dependency-free and portable across datasets.
    """

    options = options or CICModbusImportOptions()
    attack_index = (
        CICAttackLogIndex.from_csv(attack_log_csv)
        if attack_log_csv is not None
        else CICAttackLogIndex()
    )
    state_machine = ModbusTcpStateMachine()

    steps: list[BenchmarkStep] = []
    imported_rows = 0
    skipped_rows = 0
    for row_number, row in _iter_csv_rows(packet_csv):
        if options.max_rows is not None and imported_rows >= options.max_rows:
            break
        if _skip_packet_row(row, options):
            skipped_rows += 1
            continue

        event, request_hex, attack_label, notes = _event_from_packet_row(
            row,
            row_number=row_number,
            attack_index=attack_index,
            options=options,
        )
        decision, _ = state_machine.observe(event)
        steps.append(
            BenchmarkStep(
                step_id=_step_id(row, row_number, imported_rows),
                event=event,
                request_hex=request_hex,
                expected_protocol_status=decision.status,
                expected_reply_generated=(
                    False if not decision.allowed else None
                ),
                expected_world_patch_count=None,
                notes=notes,
            )
        )
        imported_rows += 1

    description = options.description or (
        "Imported CIC Modbus/tshark packet CSV as protocol-FSM benchmark "
        "steps. Attack-log labels are preserved as metadata when supplied; "
        "physical-process assertions are intentionally not inferred."
    )
    tags = [
        "public_dataset_import",
        "cic_modbus_2023",
        "modbus",
        "protocol_fsm",
    ]
    if attack_log_csv is not None:
        tags.append("attack_labeled")
    if skipped_rows:
        tags.append("responses_filtered")
    return BenchmarkCase(
        case_id=options.case_id,
        description=description,
        steps=tuple(steps),
        requires_process_context=options.requires_process_context,
        tags=tuple(tags),
    )


def recommended_tshark_fields() -> tuple[str, ...]:
    """Return a dependency-free extraction recipe for Modbus benchmark import."""

    return (
        "frame.number",
        "frame.time_epoch",
        "ip.src",
        "tcp.srcport",
        "ip.dst",
        "tcp.dstport",
        "tcp.stream",
        "mbtcp.trans_id",
        "mbtcp.unit_id",
        "modbus.func_code",
        "modbus.reference_num",
        "modbus.word_cnt",
        "modbus.bit_cnt",
        "modbus.regval_uint16",
        "modbus.data",
        "tcp.payload",
    )


def recommended_tshark_command(pcap_path: str, output_csv: str) -> str:
    fields = " ".join(f"-e {field}" for field in recommended_tshark_fields())
    return (
        f'tshark -r "{pcap_path}" -Y modbus -T fields -E header=y '
        f'-E separator=, -E quote=d -E occurrence=f {fields} > "{output_csv}"'
    )


def _event_from_packet_row(
    row: Mapping[str, str],
    *,
    row_number: int,
    attack_index: CICAttackLogIndex,
    options: CICModbusImportOptions,
) -> tuple[ICSEvent, str | None, CICAttackLabel | None, str]:
    transaction_id = _parse_optional_int(_first_value(row, _TRANSACTION_ID_ALIASES))
    unit_id = _parse_optional_int(_first_value(row, _UNIT_ID_ALIASES))
    function_code = _parse_optional_int(_first_value(row, _FUNCTION_CODE_ALIASES))
    address = _parse_optional_int(_first_value(row, _ADDRESS_ALIASES))
    count = _count_for_row(row, function_code)
    values = _values_for_row(row, function_code=function_code, count=count)
    timestamp = _first_value(row, _TIMESTAMP_ALIASES)
    source_ip = _first_value(row, _SOURCE_IP_ALIASES) or options.default_source_ip
    destination_ip = (
        _first_value(row, _DESTINATION_IP_ALIASES) or options.default_destination_ip
    )
    source_port = (
        _parse_optional_int(_first_value(row, _SOURCE_PORT_ALIASES))
        if _first_value(row, _SOURCE_PORT_ALIASES) is not None
        else options.default_source_port
    )
    destination_port = (
        _parse_optional_int(_first_value(row, _DESTINATION_PORT_ALIASES))
        if _first_value(row, _DESTINATION_PORT_ALIASES) is not None
        else options.default_destination_port
    )
    session_id = _session_id(row, options)
    actor_id = _actor_id(row, source_ip, options)
    request_hex = _request_hex_for_row(
        row,
        transaction_id=transaction_id,
        unit_id=unit_id if unit_id is not None else options.default_unit_id,
        function_code=function_code,
        address=address,
        count=count,
        values=values,
    )
    attack_label = attack_index.match(
        transaction_id=transaction_id,
        destination_ip=destination_ip,
    )

    metadata = {
        "source_format": "cic_modbus_csv",
        "import_row": row_number,
        "dataset": "CIC Modbus 2023",
    }
    if timestamp:
        metadata["dataset_timestamp"] = timestamp
    if destination_ip:
        metadata["destination_ip"] = destination_ip
    if destination_port is not None:
        metadata["destination_port"] = destination_port
    if function_code is not None:
        metadata["function_code"] = function_code
    if request_hex is not None:
        metadata["request_hex"] = request_hex
    if attack_label is not None:
        metadata.update(attack_label.metadata())
    packet_attack = _first_value(row, _ATTACK_ALIASES)
    if packet_attack and "cic_attack" not in metadata:
        metadata["cic_attack"] = packet_attack
        metadata["cic_attack_label"] = packet_attack

    notes = "Imported from CIC/tshark Modbus CSV."
    parsed_event = _try_parse_event(
        request_hex,
        session_id=session_id,
        source_ip=source_ip,
        actor_id=actor_id,
        source_port=source_port,
        destination_ip=destination_ip,
        destination_port=destination_port,
    )
    if parsed_event is not None:
        return (
            replace(
                parsed_event,
                timestamp=timestamp or parsed_event.timestamp,
                metadata={**parsed_event.metadata, **metadata},
            ),
            request_hex,
            attack_label,
            notes,
        )

    operation = _operation_for_function(function_code)
    event = ICSEvent(
        protocol="modbus",
        session_id=session_id,
        source_ip=source_ip,
        actor_id=actor_id,
        source_port=source_port,
        transaction_id=None if transaction_id is None else str(transaction_id),
        unit_id=unit_id if unit_id is not None else options.default_unit_id,
        intent=_intent_for_modbus(function_code, address),
        operation=operation,
        address=address,
        count=count,
        requested_value=_requested_value(values),
        result="observed",
        timestamp=timestamp or datetime.now(UTC).isoformat(),
        metadata=metadata,
    )
    if request_hex is None:
        notes = (
            "Imported from CSV fields without a reconstructable raw Modbus TCP "
            "request."
        )
    else:
        notes = (
            "Imported from CSV fields; raw request could not be parsed by the "
            "strict Modbus parser, so a manual event was created."
        )
    return event, request_hex, attack_label, notes


def _try_parse_event(
    request_hex: str | None,
    *,
    session_id: str,
    source_ip: str,
    actor_id: str,
    source_port: int | None,
    destination_ip: str | None,
    destination_port: int | None,
) -> ICSEvent | None:
    if request_hex is None:
        return None
    try:
        return event_from_modbus_tcp_request(
            parse_modbus_tcp_request(request_hex),
            context=ModbusHookContext(
                session_id=session_id,
                source_ip=source_ip,
                actor_id=actor_id,
                source_port=source_port,
                destination_ip=destination_ip,
                destination_port=destination_port,
            ),
        )
    except ModbusFrameError:
        return None


def _request_hex_for_row(
    row: Mapping[str, str],
    *,
    transaction_id: int | None,
    unit_id: int,
    function_code: int | None,
    address: int | None,
    count: int | None,
    values: tuple[int, ...],
) -> str | None:
    existing = _first_value(row, _REQUEST_HEX_ALIASES)
    if existing:
        cleaned = _clean_hex(existing)
        if cleaned is not None and len(cleaned) >= 16:
            return cleaned
    if transaction_id is None or function_code is None:
        return None

    data: bytes
    if function_code in {1, 2, 3, 4}:
        if address is None or count is None:
            return None
        data = address.to_bytes(2, "big") + count.to_bytes(2, "big")
    elif function_code in {5, 6}:
        if address is None or not values:
            return None
        data = address.to_bytes(2, "big") + int(values[0]).to_bytes(2, "big")
    elif function_code == 15:
        if address is None or count is None or len(values) < count:
            return None
        packed = _pack_coils(values[:count])
        data = (
            address.to_bytes(2, "big")
            + count.to_bytes(2, "big")
            + bytes([len(packed)])
            + packed
        )
    elif function_code == 16:
        if address is None or count is None or len(values) < count:
            return None
        packed = b"".join(int(value).to_bytes(2, "big") for value in values[:count])
        data = (
            address.to_bytes(2, "big")
            + count.to_bytes(2, "big")
            + bytes([len(packed)])
            + packed
        )
    else:
        data = b""
        if address is not None:
            data += address.to_bytes(2, "big")
        if count is not None:
            data += count.to_bytes(2, "big")

    pdu = bytes([function_code]) + data
    raw = bytearray()
    raw.extend(transaction_id.to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend((1 + len(pdu)).to_bytes(2, "big"))
    raw.append(unit_id)
    raw.extend(pdu)
    return bytes(raw).hex()


def _skip_packet_row(
    row: Mapping[str, str],
    options: CICModbusImportOptions,
) -> bool:
    protocol = _first_value(row, _PROTOCOL_ALIASES)
    if protocol and "modbus" not in protocol.lower():
        return True
    if options.include_responses:
        return False
    direction = _first_value(row, _DIRECTION_ALIASES)
    if direction and direction.strip().lower() in {
        "response",
        "server_to_client",
        "server->client",
        "s2c",
        "reply",
    }:
        return True
    response_to = _first_value(row, _RESPONSE_TO_ALIASES)
    if response_to:
        return True
    is_response = _first_value(row, _IS_RESPONSE_ALIASES)
    return bool(is_response and is_response.strip().lower() in {"1", "true", "yes"})


def _step_id(row: Mapping[str, str], row_number: int, imported_index: int) -> str:
    frame_number = _first_value(row, ("frame.number", "number", "packet_number"))
    transaction_id = _first_value(row, _TRANSACTION_ID_ALIASES)
    function_code = _first_value(row, _FUNCTION_CODE_ALIASES)
    parts = [
        "cic",
        f"row_{row_number:06d}",
        f"idx_{imported_index:06d}",
    ]
    if frame_number:
        parts.append(f"frame_{_safe_slug(frame_number)}")
    if transaction_id:
        parts.append(f"tid_{_safe_slug(transaction_id)}")
    if function_code:
        parts.append(f"fc_{_safe_slug(function_code)}")
    return "_".join(parts)


def _session_id(row: Mapping[str, str], options: CICModbusImportOptions) -> str:
    stream = _first_value(row, ("tcp.stream", "stream", "flow_id", "session"))
    if stream:
        return f"{options.session_id}-stream-{_safe_slug(stream)}"
    return options.session_id


def _actor_id(
    row: Mapping[str, str],
    source_ip: str,
    options: CICModbusImportOptions,
) -> str:
    explicit = _first_value(row, ("actor_id", "attacker_id", "source_actor"))
    if explicit:
        return explicit
    return f"{options.actor_id_prefix}-{_safe_slug(source_ip)}"


def _count_for_row(
    row: Mapping[str, str],
    function_code: int | None,
) -> int | None:
    count = _parse_optional_int(_first_value(row, _COUNT_ALIASES))
    if count is not None:
        return count
    if function_code in {5, 6}:
        return 1
    return None


def _values_for_row(
    row: Mapping[str, str],
    *,
    function_code: int | None,
    count: int | None,
) -> tuple[int, ...]:
    values = _parse_int_list(_first_value(row, _VALUE_ALIASES))
    if values:
        return values
    modbus_data = _first_value(row, _MODBUS_DATA_ALIASES)
    if not modbus_data:
        return ()
    data_bytes = _bytes_from_maybe_hex(modbus_data)
    if not data_bytes:
        return ()
    if function_code == 15 and count is not None:
        return _unpack_coils(data_bytes, count)
    if function_code == 16:
        return tuple(
            int.from_bytes(data_bytes[offset : offset + 2], "big")
            for offset in range(0, len(data_bytes) - 1, 2)
        )
    if function_code in {5, 6} and len(data_bytes) >= 2:
        return (int.from_bytes(data_bytes[-2:], "big"),)
    return ()


def _requested_value(values: tuple[int, ...]) -> object | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return list(values)


def _intent_for_modbus(
    function_code: int | None,
    address: int | None,
) -> Intent:
    if function_code in {1, 2, 3, 4}:
        return Intent.READ_PROCESS
    if function_code in {5, 15}:
        return Intent.CONTROL_OUTPUT
    if function_code in {6, 16} and address == 0:
        return Intent.WRITE_SETPOINT
    if function_code in {6, 16}:
        return Intent.CONTROL_OUTPUT
    return Intent.UNSUPPORTED_OPERATION


def _operation_for_function(function_code: int | None) -> str:
    return {
        1: "read_coils",
        2: "read_discrete_inputs",
        3: "read_holding_registers",
        4: "read_input_registers",
        5: "write_single_coil",
        6: "write_single_register",
        15: "write_multiple_coils",
        16: "write_multiple_registers",
    }.get(function_code, "unsupported")


def _iter_csv_rows(path: Path | str) -> Iterable[tuple[int, dict[str, str]]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=2):
            yield row_number, {str(key): value for key, value in row.items() if key}


def _first_value(
    row: Mapping[str, str],
    aliases: Iterable[str],
) -> str | None:
    normalized_aliases = {_normalize_key(alias) for alias in aliases}
    for key, value in row.items():
        if _normalize_key(key) in normalized_aliases:
            normalized = _normalize_cell(value)
            if normalized is not None:
                return normalized
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _normalize_cell(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"na", "nan", "none", "null", "-"}:
        return None
    return text


def _parse_optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        try:
            parsed = float(text)
        except ValueError:
            return None
        if parsed.is_integer():
            return int(parsed)
    return None


def _parse_int_list(value: object | None) -> tuple[int, ...]:
    if value is None:
        return ()
    text = str(value).strip()
    if not text:
        return ()
    tokens = [
        token
        for token in re.split(r"[\s,;|]+", text.strip("[](){}"))
        if token.strip()
    ]
    values: list[int] = []
    for token in tokens:
        parsed = _parse_optional_int(token)
        if parsed is None:
            return ()
        values.append(parsed)
    return tuple(values)


def _transaction_id_key(value: str | int) -> str:
    parsed = _parse_optional_int(value)
    return str(parsed) if parsed is not None else str(value).strip()


def _safe_slug(value: object) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip()).strip("-")
    return slug or "unknown"


def _clean_hex(value: str) -> str | None:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(cleaned) % 2:
        return None
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        return None
    return cleaned.lower()


def _bytes_from_maybe_hex(value: str) -> bytes | None:
    cleaned = _clean_hex(value)
    if cleaned is None:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


def _pack_coils(values: tuple[int, ...]) -> bytes:
    packed = bytearray()
    for offset in range(0, len(values), 8):
        byte_value = 0
        for bit, value in enumerate(values[offset : offset + 8]):
            if int(bool(value)):
                byte_value |= 1 << bit
        packed.append(byte_value)
    return bytes(packed)


def _unpack_coils(packed_values: bytes, count: int) -> tuple[int, ...]:
    values: list[int] = []
    for byte in packed_values:
        for bit in range(8):
            if len(values) == count:
                return tuple(values)
            values.append((byte >> bit) & 1)
    return tuple(values)


_PROTOCOL_ALIASES = ("protocol", "_ws.col.Protocol", "highest_layer")
_TIMESTAMP_ALIASES = (
    "Timestamp",
    "timestamp",
    "time",
    "frame.time",
    "frame.time_epoch",
)
_SOURCE_IP_ALIASES = ("ip.src", "src_ip", "source_ip", "SourceIP", "Source IP")
_SOURCE_PORT_ALIASES = (
    "tcp.srcport",
    "src_port",
    "source_port",
    "SourcePort",
)
_DESTINATION_IP_ALIASES = (
    "ip.dst",
    "dst_ip",
    "destination_ip",
    "TargetIP",
    "Target IP",
)
_DESTINATION_PORT_ALIASES = (
    "tcp.dstport",
    "dst_port",
    "destination_port",
    "DestinationPort",
)
_TARGET_IP_ALIASES = ("TargetIP", "Target IP", "target_ip", "ip.dst")
_ATTACK_ALIASES = ("Attack", "attack", "Label", "label", "attack_label")
_TRANSACTION_ID_ALIASES = (
    "TransactionID",
    "Transaction ID",
    "transaction_id",
    "trans_id",
    "tid",
    "mbtcp.trans_id",
    "mbtcp.transaction_id",
    "modbus.transaction_id",
)
_UNIT_ID_ALIASES = (
    "mbtcp.unit_id",
    "modbus.unit_id",
    "unit_id",
    "unit",
    "slave_id",
)
_FUNCTION_CODE_ALIASES = (
    "modbus.func_code",
    "modbus.function_code",
    "function_code",
    "func_code",
    "fc",
)
_ADDRESS_ALIASES = (
    "modbus.reference_num",
    "modbus.starting_address",
    "reference_num",
    "address",
    "addr",
    "register",
    "start_address",
)
_COUNT_ALIASES = (
    "modbus.word_cnt",
    "modbus.bit_cnt",
    "modbus.quantity",
    "word_cnt",
    "bit_cnt",
    "quantity",
    "count",
    "register_count",
    "num_registers",
)
_VALUE_ALIASES = (
    "modbus.regval_uint16",
    "modbus.output_value",
    "write_value",
    "requested_value",
    "values",
    "value",
)
_MODBUS_DATA_ALIASES = ("modbus.data", "data", "data.data")
_REQUEST_HEX_ALIASES = (
    "request_hex",
    "payload_hex",
    "raw_hex",
    "mbtcp.payload",
    "tcp.payload",
    "frame_raw",
)
_DIRECTION_ALIASES = ("direction", "flow_direction", "message_type", "kind")
_RESPONSE_TO_ALIASES = ("modbus.response_to", "response_to", "responseTo")
_IS_RESPONSE_ALIASES = ("is_response", "modbus.is_response")
