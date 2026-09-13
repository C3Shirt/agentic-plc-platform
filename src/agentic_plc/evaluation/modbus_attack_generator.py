from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Mapping
from uuid import NAMESPACE_URL, uuid5

from agentic_plc.adapters import ModbusHookContext, event_from_modbus_tcp_request
from agentic_plc.agent.protocol_state_machine import ProtocolTransitionStatus
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.evaluation.consistency_benchmark import BenchmarkCase, BenchmarkStep
from agentic_plc.evaluation.physical_invariants import (
    ProcessInvariant,
    ProcessInvariantKind,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_read_request,
    build_modbus_tcp_write_multiple_coils_request,
    build_modbus_tcp_write_multiple_registers_request,
    build_modbus_tcp_write_single_coil_request,
    build_modbus_tcp_write_single_register_request,
)


@dataclass(frozen=True, slots=True)
class ModbusTableSpec:
    name: str
    function_code: int
    mapped_size: int


TANK_PUMP_TABLES: tuple[ModbusTableSpec, ...] = (
    ModbusTableSpec("coils", function_code=1, mapped_size=2),
    ModbusTableSpec("discrete_inputs", function_code=2, mapped_size=1),
    ModbusTableSpec("holding_registers", function_code=3, mapped_size=2),
    ModbusTableSpec("input_registers", function_code=4, mapped_size=2),
)


@dataclass(frozen=True, slots=True)
class ModbusAttackGenerationOptions:
    """Controls deterministic live Modbus attack-flow generation.

    The defaults target the local tank-pump PLC slice. A future process/backend can
    reuse the same generator shape by supplying different table specs and
    HMI/register bindings.
    """

    seed: int = 1337
    profile: str = "tank_pump"
    unit_id: int = 1
    transaction_start: int = 700
    scan_start: int = 0
    scan_stop: int = 6
    tables: tuple[ModbusTableSpec, ...] = TANK_PUMP_TABLES
    setpoint_register_address: int = 0
    mode_register_address: int = 1
    pump_coil_address: int = 0
    inlet_coil_address: int = 1
    setpoint_variable: str = "level_sp"
    pump_variable: str = "pump_cmd"
    inlet_variable: str = "inlet_valve_open"
    encoded_setpoint_scale: float = 0.1
    setpoint_values: tuple[int, ...] = (550, 720, 650)
    coil_values: tuple[bool, ...] = (True, False, True)
    include_recon_sweep: bool = True
    include_write_readback: bool = True
    include_batch_writes: bool = True
    include_exception_probing: bool = True
    include_transaction_abuse: bool = True
    include_multi_session_interleave: bool = True

    def validate(self) -> None:
        if not 0 <= self.unit_id <= 247:
            raise ValueError("unit_id must be between 0 and 247")
        if not 0 <= self.transaction_start <= 65535:
            raise ValueError("transaction_start must fit in uint16")
        if self.scan_start < 0 or self.scan_stop <= self.scan_start:
            raise ValueError("scan range must be non-empty and non-negative")
        max_transactions = self.transaction_start + 256
        if max_transactions > 65535:
            raise ValueError("transaction_start leaves too little uint16 headroom")
        if not self.tables:
            raise ValueError("at least one Modbus table spec is required")
        for table in self.tables:
            if table.function_code not in {1, 2, 3, 4}:
                raise ValueError("table function_code must be 1, 2, 3, or 4")
            if table.mapped_size < 0:
                raise ValueError("table mapped_size must be non-negative")
        for address in (
            self.setpoint_register_address,
            self.mode_register_address,
            self.pump_coil_address,
            self.inlet_coil_address,
        ):
            if not 0 <= address <= 65535:
                raise ValueError("mapped Modbus addresses must fit in uint16")
        if self.mode_register_address != self.setpoint_register_address + 1:
            raise ValueError(
                "batch register write requires mode_register_address to follow "
                "setpoint_register_address"
            )
        if self.inlet_coil_address != self.pump_coil_address + 1:
            raise ValueError(
                "batch coil write requires inlet_coil_address to follow "
                "pump_coil_address"
            )
        for value in self.setpoint_values:
            if not 0 <= int(value) <= 65535:
                raise ValueError("setpoint_values must fit in uint16")


def build_generated_modbus_attack_cases(
    options: ModbusAttackGenerationOptions | None = None,
) -> tuple[BenchmarkCase, ...]:
    """Generate deterministic attack-like live Modbus benchmark cases."""

    options = options or ModbusAttackGenerationOptions()
    options.validate()
    allocator = _TransactionAllocator(options.transaction_start)
    rng = random.Random(options.seed)

    cases: list[BenchmarkCase] = []
    if options.include_recon_sweep:
        cases.append(_build_recon_sweep(options, allocator, rng))
    if options.include_write_readback:
        cases.append(_build_write_readback(options, allocator))
    if options.include_batch_writes:
        cases.append(_build_batch_writes(options, allocator))
    if options.include_exception_probing:
        cases.append(_build_exception_probing(options, allocator))
    if options.include_transaction_abuse:
        cases.append(_build_transaction_abuse(options, allocator))
    if options.include_multi_session_interleave:
        cases.append(_build_multi_session_interleave(options, allocator))
    return tuple(cases)


def modbus_attack_generation_metadata(
    options: ModbusAttackGenerationOptions | None = None,
) -> dict[str, object]:
    options = options or ModbusAttackGenerationOptions()
    return {
        "generator": "agentic_plc.evaluation.modbus_attack_generator",
        "profile": options.profile,
        "seed": options.seed,
        "unit_id": options.unit_id,
        "transaction_start": options.transaction_start,
        "scan_start": options.scan_start,
        "scan_stop": options.scan_stop,
        "tables": [
            {
                "name": table.name,
                "function_code": table.function_code,
                "mapped_size": table.mapped_size,
            }
            for table in options.tables
        ],
        "bindings": {
            "setpoint_register_address": options.setpoint_register_address,
            "mode_register_address": options.mode_register_address,
            "pump_coil_address": options.pump_coil_address,
            "inlet_coil_address": options.inlet_coil_address,
            "setpoint_variable": options.setpoint_variable,
            "pump_variable": options.pump_variable,
            "inlet_variable": options.inlet_variable,
            "encoded_setpoint_scale": options.encoded_setpoint_scale,
        },
        "setpoint_values": list(options.setpoint_values),
        "coil_values": list(options.coil_values),
        "features": {
            "recon_sweep": options.include_recon_sweep,
            "write_readback": options.include_write_readback,
            "batch_writes": options.include_batch_writes,
            "exception_probing": options.include_exception_probing,
            "transaction_abuse": options.include_transaction_abuse,
            "multi_session_interleave": options.include_multi_session_interleave,
        },
    }


def _build_recon_sweep(
    options: ModbusAttackGenerationOptions,
    allocator: "_TransactionAllocator",
    rng: random.Random,
) -> BenchmarkCase:
    steps: list[BenchmarkStep] = []
    addresses = list(range(options.scan_start, options.scan_stop))
    for table in options.tables:
        shuffled = list(addresses)
        rng.shuffle(shuffled)
        for address in shuffled:
            in_range = address < table.mapped_size
            steps.append(
                _step_from_request(
                    step_id=f"scan_{table.name}_{address}",
                    request_hex=build_modbus_tcp_read_request(
                        transaction_id=allocator.take(),
                        unit_id=options.unit_id,
                        function_code=table.function_code,
                        address=address,
                        count=1,
                    ),
                    session_id="generated-recon-session",
                    actor_id="generated-recon-actor",
                    expected_response_kind="normal" if in_range else "exception",
                    expected_exception_code=None if in_range else 2,
                    process_invariants=(
                        (
                            _reply_matches_snapshot(
                                f"scan_{table.name}_{address}_matches_hmi"
                            ),
                        )
                        if in_range and table.function_code in {1, 2, 3}
                        else ()
                    ),
                    notes=(
                        "Generated reconnaissance table sweep. Out-of-range "
                        "addresses should be handled as Modbus exception code 2."
                    ),
                )
            )
    return BenchmarkCase(
        case_id=f"generated_modbus_recon_sweep_seed_{options.seed}",
        description=(
            "Seeded reconnaissance sweep over every Modbus table, mixing mapped "
            "and unmapped addresses."
        ),
        requires_process_context=False,
        tags=("generated", "live", "modbus", "recon", "scan", "exception"),
        steps=tuple(steps),
    )


def _build_write_readback(
    options: ModbusAttackGenerationOptions,
    allocator: "_TransactionAllocator",
) -> BenchmarkCase:
    steps: list[BenchmarkStep] = []
    for index, value in enumerate(options.setpoint_values):
        percent = int(value) * options.encoded_setpoint_scale
        steps.append(
            _step_from_request(
                step_id=f"write_setpoint_{value}",
                request_hex=build_modbus_tcp_write_single_register_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    address=options.setpoint_register_address,
                    value=int(value),
                ),
                session_id="generated-write-session",
                actor_id="generated-control-actor",
                expected_process_values={options.setpoint_variable: percent},
                process_invariants=(
                    ProcessInvariant(
                        invariant_id=f"setpoint_{index}_equals_hmi",
                        kind=ProcessInvariantKind.VARIABLE_EQUALS,
                        variable_id=options.setpoint_variable,
                        value=percent,
                        description="Setpoint write should be visible through HMI.",
                    ),
                    ProcessInvariant(
                        invariant_id=f"setpoint_{index}_within_bounds",
                        kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                        variable_id=options.setpoint_variable,
                        description="Generated setpoint should stay process-bounded.",
                    ),
                ),
            )
        )
        steps.append(
            _step_from_request(
                step_id=f"readback_setpoint_{value}",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.setpoint_register_address,
                    count=1,
                ),
                session_id="generated-write-session",
                actor_id="generated-control-actor",
                expected_reply_values=(int(value),),
                expected_process_values={options.setpoint_variable: percent},
                process_invariants=(
                    _reply_matches_snapshot(f"setpoint_{index}_readback_matches_hmi"),
                ),
            )
        )

    for index, energized in enumerate(options.coil_values):
        numeric = 1.0 if energized else 0.0
        suffix = "on" if energized else "off"
        steps.append(
            _step_from_request(
                step_id=f"write_pump_coil_{index}_{suffix}",
                request_hex=build_modbus_tcp_write_single_coil_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    address=options.pump_coil_address,
                    energized=energized,
                ),
                session_id="generated-write-session",
                actor_id="generated-control-actor",
                expected_process_values={options.pump_variable: numeric},
                process_invariants=(
                    ProcessInvariant(
                        invariant_id=f"pump_coil_{index}_{suffix}_visible_hmi",
                        kind=ProcessInvariantKind.VARIABLE_EQUALS,
                        variable_id=options.pump_variable,
                        value=numeric,
                        description="Pump coil command should be visible via HMI.",
                    ),
                ),
            )
        )
        steps.append(
            _step_from_request(
                step_id=f"readback_pump_coil_{index}_{suffix}",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    function_code=1,
                    address=options.pump_coil_address,
                    count=1,
                ),
                session_id="generated-write-session",
                actor_id="generated-control-actor",
                expected_reply_values=(int(energized),),
                expected_process_values={options.pump_variable: numeric},
                process_invariants=(
                    _reply_matches_snapshot(f"pump_coil_{index}_readback_matches_hmi"),
                ),
            )
        )
    return BenchmarkCase(
        case_id=f"generated_modbus_write_readback_seed_{options.seed}",
        description=(
            "Seeded control-write sequence with immediate protocol and HMI "
            "read-back checks."
        ),
        requires_process_context=False,
        tags=("generated", "live", "modbus", "write_readback", "control"),
        steps=tuple(steps),
    )


def _build_batch_writes(
    options: ModbusAttackGenerationOptions,
    allocator: "_TransactionAllocator",
) -> BenchmarkCase:
    setpoint = options.setpoint_values[-1] if options.setpoint_values else 650
    scaled_setpoint = int(setpoint) * options.encoded_setpoint_scale
    coil_pair = (
        bool(options.coil_values[-1]) if options.coil_values else True,
        True,
    )
    return BenchmarkCase(
        case_id=f"generated_modbus_batch_writes_seed_{options.seed}",
        description=(
            "Batch-write multiple holding registers and coils, then verify "
            "read-back over the same live protocol surface."
        ),
        requires_process_context=False,
        tags=("generated", "live", "modbus", "batch_write", "write_readback"),
        steps=(
            _step_from_request(
                step_id="batch_write_setpoint_and_mode",
                request_hex=build_modbus_tcp_write_multiple_registers_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    address=options.setpoint_register_address,
                    values=(int(setpoint), 2),
                ),
                session_id="generated-batch-session",
                actor_id="generated-control-actor",
                expected_process_values={options.setpoint_variable: scaled_setpoint},
                process_invariants=(
                    ProcessInvariant(
                        invariant_id="batch_setpoint_visible_hmi",
                        kind=ProcessInvariantKind.VARIABLE_EQUALS,
                        variable_id=options.setpoint_variable,
                        value=scaled_setpoint,
                        description="Batch register write should update HMI setpoint.",
                    ),
                ),
            ),
            _step_from_request(
                step_id="batch_readback_setpoint_and_mode",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.setpoint_register_address,
                    count=2,
                ),
                session_id="generated-batch-session",
                actor_id="generated-control-actor",
                expected_reply_values=(int(setpoint), 2),
                expected_process_values={options.setpoint_variable: scaled_setpoint},
                process_invariants=(
                    _reply_matches_snapshot("batch_register_readback_matches_hmi"),
                ),
            ),
            _step_from_request(
                step_id="batch_write_pump_and_inlet_coils",
                request_hex=build_modbus_tcp_write_multiple_coils_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    address=options.pump_coil_address,
                    values=coil_pair,
                ),
                session_id="generated-batch-session",
                actor_id="generated-control-actor",
                expected_process_values={
                    options.pump_variable: 1.0 if coil_pair[0] else 0.0,
                    options.inlet_variable: 1.0 if coil_pair[1] else 0.0,
                },
            ),
            _step_from_request(
                step_id="batch_readback_pump_and_inlet_coils",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    function_code=1,
                    address=options.pump_coil_address,
                    count=2,
                ),
                session_id="generated-batch-session",
                actor_id="generated-control-actor",
                expected_reply_values=tuple(int(value) for value in coil_pair),
                expected_process_values={
                    options.pump_variable: 1.0 if coil_pair[0] else 0.0,
                    options.inlet_variable: 1.0 if coil_pair[1] else 0.0,
                },
                process_invariants=(
                    _reply_matches_snapshot("batch_coil_readback_matches_hmi"),
                ),
            ),
        ),
    )


def _build_exception_probing(
    options: ModbusAttackGenerationOptions,
    allocator: "_TransactionAllocator",
) -> BenchmarkCase:
    invalid_address = max(options.scan_stop, 99)
    return BenchmarkCase(
        case_id=f"generated_modbus_exception_probing_seed_{options.seed}",
        description=(
            "Syntactically valid probes against unmapped addresses; expected "
            "behavior is well-formed Modbus exception responses."
        ),
        requires_process_context=False,
        tags=("generated", "live", "modbus", "exception", "invalid_address"),
        steps=(
            _step_from_request(
                step_id="probe_unmapped_holding_read",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    function_code=3,
                    address=invalid_address,
                    count=1,
                ),
                session_id="generated-probe-session",
                actor_id="generated-probe-actor",
                expected_response_kind="exception",
                expected_exception_code=2,
            ),
            _step_from_request(
                step_id="probe_unmapped_single_register_write",
                request_hex=build_modbus_tcp_write_single_register_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    address=invalid_address,
                    value=1234,
                ),
                session_id="generated-probe-session",
                actor_id="generated-probe-actor",
                expected_response_kind="exception",
                expected_exception_code=2,
            ),
            _step_from_request(
                step_id="probe_unmapped_single_coil_write",
                request_hex=build_modbus_tcp_write_single_coil_request(
                    transaction_id=allocator.take(),
                    unit_id=options.unit_id,
                    address=invalid_address,
                    energized=True,
                ),
                session_id="generated-probe-session",
                actor_id="generated-probe-actor",
                expected_response_kind="exception",
                expected_exception_code=2,
            ),
        ),
    )


def _build_transaction_abuse(
    options: ModbusAttackGenerationOptions,
    allocator: "_TransactionAllocator",
) -> BenchmarkCase:
    transaction_id = allocator.take()
    return BenchmarkCase(
        case_id=f"generated_modbus_transaction_abuse_seed_{options.seed}",
        description=(
            "Reuse one transaction id for two different requests in the same "
            "session. The response should remain parseable, while FSM telemetry "
            "should mark the second request anomalous."
        ),
        requires_process_context=False,
        tags=("generated", "live", "modbus", "protocol_fsm", "anomaly"),
        steps=(
            _step_from_request(
                step_id="transaction_abuse_baseline",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=transaction_id,
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.setpoint_register_address,
                    count=1,
                ),
                session_id="generated-transaction-abuse-session",
                actor_id="generated-abuse-actor",
                expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
            ),
            _step_from_request(
                step_id="transaction_abuse_reuse_different_address",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=transaction_id,
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.mode_register_address,
                    count=1,
                ),
                session_id="generated-transaction-abuse-session",
                actor_id="generated-abuse-actor",
                expected_protocol_status=ProtocolTransitionStatus.ANOMALOUS,
            ),
        ),
    )


def _build_multi_session_interleave(
    options: ModbusAttackGenerationOptions,
    allocator: "_TransactionAllocator",
) -> BenchmarkCase:
    transaction_id = allocator.take()
    return BenchmarkCase(
        case_id=f"generated_modbus_multi_session_interleave_seed_{options.seed}",
        description=(
            "Interleave two logical attacker sessions that reuse the same "
            "transaction id independently. Correct FSM tracking should not leak "
            "transaction state across sessions."
        ),
        requires_process_context=False,
        tags=("generated", "live", "modbus", "multi_session", "interleave"),
        steps=(
            _step_from_request(
                step_id="session_a_tid_read_address_0",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=transaction_id,
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.setpoint_register_address,
                    count=1,
                ),
                session_id="generated-session-a",
                actor_id="generated-actor-a",
                expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
            ),
            _step_from_request(
                step_id="session_b_same_tid_read_address_1",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=transaction_id,
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.mode_register_address,
                    count=1,
                ),
                session_id="generated-session-b",
                actor_id="generated-actor-b",
                expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
            ),
            _step_from_request(
                step_id="session_a_repeats_same_tid_same_request",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=transaction_id,
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.setpoint_register_address,
                    count=1,
                ),
                session_id="generated-session-a",
                actor_id="generated-actor-a",
                expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
            ),
            _step_from_request(
                step_id="session_b_repeats_same_tid_same_request",
                request_hex=build_modbus_tcp_read_request(
                    transaction_id=transaction_id,
                    unit_id=options.unit_id,
                    function_code=3,
                    address=options.mode_register_address,
                    count=1,
                ),
                session_id="generated-session-b",
                actor_id="generated-actor-b",
                expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
            ),
        ),
    )


def _step_from_request(
    *,
    step_id: str,
    request_hex: str,
    session_id: str,
    actor_id: str,
    expected_protocol_status: ProtocolTransitionStatus = (
        ProtocolTransitionStatus.ALLOWED
    ),
    expected_response_kind: str = "normal",
    expected_exception_code: int | None = None,
    expected_process_values: Mapping[str, float] | None = None,
    expected_reply_values: tuple[int, ...] | None = None,
    process_invariants: tuple[ProcessInvariant, ...] = (),
    notes: str = "",
) -> BenchmarkStep:
    event = _event_from_request(
        request_hex,
        step_id=step_id,
        session_id=session_id,
        actor_id=actor_id,
    )
    return BenchmarkStep(
        step_id=step_id,
        request_hex=request_hex,
        event=event,
        expected_protocol_status=expected_protocol_status,
        expected_reply_generated=None,
        expected_world_patch_count=None,
        expected_process_values=dict(expected_process_values or {}),
        expected_reply_values=expected_reply_values,
        expected_response_kind=expected_response_kind,
        expected_exception_code=expected_exception_code,
        process_invariants=process_invariants,
        notes=notes,
    )


def _event_from_request(
    request_hex: str,
    *,
    step_id: str,
    session_id: str,
    actor_id: str,
) -> ICSEvent:
    event = event_from_modbus_tcp_request(
        bytes.fromhex(request_hex),
        context=ModbusHookContext(
            session_id=session_id,
            source_ip="192.0.2.10",
            actor_id=actor_id,
            source_port=55020,
            destination_ip="192.0.2.20",
            destination_port=502,
        ),
    )
    stable_name = "|".join(
        (
            "agentic-plc.generated-modbus-attack-event",
            step_id,
            session_id,
            actor_id,
            request_hex,
        )
    )
    return replace(
        event,
        event_id=str(uuid5(NAMESPACE_URL, stable_name)),
        timestamp="2026-01-01T00:00:00+00:00",
    )


def _reply_matches_snapshot(invariant_id: str) -> ProcessInvariant:
    return ProcessInvariant(
        invariant_id=invariant_id,
        kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
        description="Observed Modbus reply should match the HMI process snapshot.",
    )


class _TransactionAllocator:
    def __init__(self, start: int) -> None:
        self._next = int(start)

    def take(self) -> int:
        value = self._next
        self._next += 1
        if value > 65535:
            raise ValueError("transaction id exhausted")
        return value
