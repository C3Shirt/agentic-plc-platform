from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from agentic_plc.adapters import (
    ModbusHookContext,
    event_from_modbus_tcp_request,
)
from agentic_plc.agent import AgentRuntime
from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.agent.protocol_state_machine import ProtocolTransitionStatus
from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.processes import (
    ProcessRegisterMap,
    ProcessVariable,
    ScenarioMapping,
    TraceProcessBackend,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_read_registers_response,
    parse_modbus_tcp_frame,
)
from agentic_plc.telemetry import InMemoryEventLog
from agentic_plc.telemetry.serialization import event_to_dict


ProcessContextFactory = Callable[[], PhysicalProcessContext]


@dataclass(frozen=True, slots=True)
class BenchmarkStep:
    """One protocol-facing benchmark action plus expected consistency checks."""

    step_id: str
    event: ICSEvent
    expected_protocol_status: ProtocolTransitionStatus
    request_hex: str | None = None
    expected_reply_generated: bool | None = None
    expected_world_patch_count: int | None = None
    expected_process_values: Mapping[str, float] = field(default_factory=dict)
    expected_reply_values: tuple[int, ...] | None = None
    forced_reply_hex: str | None = None
    expected_forced_reply_accepted: bool | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "request_hex": self.request_hex,
            "event": event_to_dict(self.event),
            "expected_protocol_status": self.expected_protocol_status.value,
            "expected_reply_generated": self.expected_reply_generated,
            "expected_world_patch_count": self.expected_world_patch_count,
            "expected_process_values": dict(self.expected_process_values),
            "expected_reply_values": (
                list(self.expected_reply_values)
                if self.expected_reply_values is not None
                else None
            ),
            "forced_reply_hex": self.forced_reply_hex,
            "expected_forced_reply_accepted": self.expected_forced_reply_accepted,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """A reusable interaction trace for honeypot consistency evaluation."""

    case_id: str
    description: str
    steps: tuple[BenchmarkStep, ...]
    requires_process_context: bool = True
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "requires_process_context": self.requires_process_context,
            "tags": list(self.tags),
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class BenchmarkStepResult:
    step_id: str
    protocol_status: str
    protocol_reason: str | None
    protocol_allowed: bool
    interaction_phase: str | None
    reply_generated: bool
    reply_values: tuple[int, ...] | None
    world_patch_count: int
    rejected: tuple[dict[str, str], ...]
    process_values: Mapping[str, float]
    forced_reply_accepted: bool | None = None
    forced_reply_error: str | None = None
    checks: Mapping[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "protocol_status": self.protocol_status,
            "protocol_reason": self.protocol_reason,
            "protocol_allowed": self.protocol_allowed,
            "interaction_phase": self.interaction_phase,
            "reply_generated": self.reply_generated,
            "reply_values": (
                list(self.reply_values) if self.reply_values is not None else None
            ),
            "world_patch_count": self.world_patch_count,
            "rejected": list(self.rejected),
            "process_values": dict(self.process_values),
            "forced_reply_accepted": self.forced_reply_accepted,
            "forced_reply_error": self.forced_reply_error,
            "checks": dict(self.checks),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    cases: Mapping[str, tuple[BenchmarkStepResult, ...]]

    @property
    def total_steps(self) -> int:
        return sum(len(results) for results in self.cases.values())

    @property
    def passed_steps(self) -> int:
        return sum(
            1
            for results in self.cases.values()
            for result in results
            if result.passed
        )

    @property
    def passed(self) -> bool:
        return self.passed_steps == self.total_steps

    def status_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for results in self.cases.values():
            for result in results:
                counts[result.protocol_status] += 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "passed_steps": self.passed_steps,
            "total_steps": self.total_steps,
            "protocol_status_counts": self.status_counts(),
            "cases": {
                case_id: [result.to_dict() for result in results]
                for case_id, results in self.cases.items()
            },
        }


class ConsistencyBenchmarkRunner:
    """Run protocol/physical consistency cases against the local runtime."""

    def __init__(
        self,
        *,
        process_context_factory: ProcessContextFactory | None = None,
    ) -> None:
        self._process_context_factory = (
            process_context_factory or create_benchmark_process_context
        )

    def run(
        self,
        cases: tuple[BenchmarkCase, ...] | None = None,
    ) -> BenchmarkReport:
        cases = cases or build_default_modbus_consistency_cases()
        return BenchmarkReport(
            cases={case.case_id: self.run_case(case) for case in cases}
        )

    def run_case(self, case: BenchmarkCase) -> tuple[BenchmarkStepResult, ...]:
        context = (
            self._process_context_factory()
            if case.requires_process_context
            else None
        )
        event_log = InMemoryEventLog()
        runtime = AgentRuntime(
            process_context=context,
            event_store=event_log,
        )

        results: list[BenchmarkStepResult] = []
        for step in case.steps:
            decision = runtime.observe_and_decide(step.event)
            observed_event = event_log.list_events()[-1]
            protocol_status = str(
                observed_event.metadata.get("protocol_fsm_status", "missing")
            )
            protocol_reason = observed_event.metadata.get("protocol_fsm_reason")
            reply_values = _reply_values(decision.protocol_replies[0].payload_hex) if decision.protocol_replies else None
            process_values = _read_process_values(
                context,
                step.expected_process_values.keys(),
            )
            forced_reply_accepted, forced_reply_error = _validate_forced_reply(
                step,
                observed_event,
            )
            checks = _checks_for_step(
                step,
                protocol_status=protocol_status,
                reply_generated=bool(decision.protocol_replies),
                world_patch_count=len(decision.world_patches),
                process_values=process_values,
                reply_values=reply_values,
                forced_reply_accepted=forced_reply_accepted,
            )
            results.append(
                BenchmarkStepResult(
                    step_id=step.step_id,
                    protocol_status=protocol_status,
                    protocol_reason=str(protocol_reason) if protocol_reason else None,
                    protocol_allowed=bool(
                        observed_event.metadata.get("protocol_fsm_allowed")
                    ),
                    interaction_phase=_optional_text(
                        observed_event.metadata.get("interaction_phase")
                    ),
                    reply_generated=bool(decision.protocol_replies),
                    reply_values=reply_values,
                    world_patch_count=len(decision.world_patches),
                    rejected=tuple(
                        {
                            "kind": item.kind,
                            "error": item.error,
                        }
                        for item in decision.rejected
                    ),
                    process_values=process_values,
                    forced_reply_accepted=forced_reply_accepted,
                    forced_reply_error=forced_reply_error,
                    checks=checks,
                )
            )
        return tuple(results)


def build_default_modbus_consistency_cases() -> tuple[BenchmarkCase, ...]:
    """Build a compact public baseline for our first consistency benchmark.

    The cases intentionally mix protocol-only checks, physical consistency
    checks, and generated-reply gating. They are synthetic, reproducible, and
    do not require connecting to a real PLC or production network.
    """

    return (
        BenchmarkCase(
            case_id="modbus_valid_scan",
            description=(
                "Valid Modbus TCP reads over adjacent registers should stay in "
                "the ready state and produce generated read replies."
            ),
            tags=("protocol_fsm", "read", "scan"),
            steps=tuple(
                _modbus_step_from_request(
                    step_id=f"read_holding_{address}",
                    request_hex=_modbus_read_holding_request(
                        transaction_id=100 + address,
                        address=address,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                )
                for address in range(3)
            ),
        ),
        BenchmarkCase(
            case_id="modbus_transaction_reuse_anomaly",
            description=(
                "A reused transaction id with a different request is suspicious "
                "but still servable for Modbus TCP."
            ),
            tags=("protocol_fsm", "transaction_id", "anomaly"),
            steps=(
                _modbus_step_from_request(
                    step_id="initial_read_tid_200",
                    request_hex=_modbus_read_holding_request(
                        transaction_id=200,
                        address=0,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                ),
                _modbus_step_from_request(
                    step_id="reused_tid_different_address",
                    request_hex=_modbus_read_holding_request(
                        transaction_id=200,
                        address=2,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ANOMALOUS,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                ),
            ),
        ),
        BenchmarkCase(
            case_id="modbus_write_then_readback",
            description=(
                "A protocol-valid write to a mapped setpoint must mutate the "
                "physical backend before the acknowledgement is released, and "
                "the next read must reflect the new encoded value."
            ),
            tags=("protocol_fsm", "physical_consistency", "write_readback"),
            steps=(
                _modbus_step_from_request(
                    step_id="write_level_setpoint_70_percent",
                    request_hex=_modbus_write_single_register_request(
                        transaction_id=300,
                        address=0,
                        value=700,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=1,
                    expected_process_values={"level_sp": 70.0},
                ),
                _modbus_step_from_request(
                    step_id="read_back_level_setpoint",
                    request_hex=_modbus_read_holding_request(
                        transaction_id=301,
                        address=0,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                    expected_process_values={"level_sp": 70.0},
                    expected_reply_values=(700,),
                ),
            ),
        ),
        BenchmarkCase(
            case_id="modbus_protocol_valid_physical_invalid",
            description=(
                "A syntactically valid Modbus write can still be physically "
                "invalid; the runtime should observe the request but withhold a "
                "generated write acknowledgement."
            ),
            tags=("protocol_fsm", "physical_consistency", "invalid_write"),
            steps=(
                _modbus_step_from_request(
                    step_id="write_level_setpoint_out_of_range",
                    request_hex=_modbus_write_single_register_request(
                        transaction_id=400,
                        address=0,
                        value=2000,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=False,
                    expected_world_patch_count=0,
                    expected_process_values={"level_sp": 50.0},
                ),
            ),
        ),
        BenchmarkCase(
            case_id="modbus_denied_transition_blocks_forced_reply",
            description=(
                "If the protocol FSM denies a transition, even a well-formed "
                "generated reply must be rejected by the reply validator."
            ),
            requires_process_context=False,
            tags=("protocol_fsm", "generated_reply_gate", "denied"),
            steps=(
                BenchmarkStep(
                    step_id="unsupported_function_99",
                    event=_manual_modbus_event(
                        intent=Intent.UNSUPPORTED_OPERATION,
                        operation="function_99",
                        transaction_id=500,
                        function_code=99,
                        address=0,
                        count=1,
                    ),
                    request_hex=_modbus_request_frame_hex(
                        transaction_id=500,
                        function_code=99,
                        data=bytes.fromhex("00 00 00 01"),
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.DENIED,
                    expected_reply_generated=False,
                    expected_world_patch_count=0,
                    forced_reply_hex=build_modbus_tcp_read_registers_response(
                        transaction_id=500,
                        unit_id=1,
                        function_code=3,
                        values=[500],
                    ),
                    expected_forced_reply_accepted=False,
                    notes=(
                        "The raw packet is included for traceability, but the "
                        "event is manual because the Modbus parser rejects "
                        "unsupported functions before adapter normalization."
                    ),
                ),
            ),
        ),
    )


def create_benchmark_process_context() -> PhysicalProcessContext:
    backend = TraceProcessBackend(
        process_id="benchmark_tank_process",
        name="benchmark_trace",
        time_seconds=[0.0],
        variables=[
            ProcessVariable(
                variable_id="level_sp",
                name="Level setpoint",
                role="setpoint",
                unit="%",
                minimum=0.0,
                maximum=100.0,
                writable=True,
            ),
            ProcessVariable(
                variable_id="level_pct",
                name="Level measurement",
                role="measurement",
                unit="%",
                minimum=0.0,
                maximum=100.0,
                writable=False,
            ),
            ProcessVariable(
                variable_id="pump_cmd",
                name="Pump command",
                role="manipulated_variable",
                minimum=0.0,
                maximum=1.0,
                writable=True,
            ),
        ],
        series={
            "level_sp": (50.0,),
            "level_pct": (48.0,),
            "pump_cmd": (1.0,),
        },
    )
    scenario = ScenarioMapping.from_dict(
        {
            "scenario_id": "benchmark_modbus_tank_slice",
            "process_id": "benchmark_tank_process",
            "backend": {"type": "trace"},
            "plc_area": "benchmark_cell",
            "description": "Minimal benchmark PLC slice for consistency tests.",
            "points": [
                {
                    "variable_id": "level_sp",
                    "protocol": "modbus",
                    "table": "holding_registers",
                    "address": 0,
                    "access": "read_write",
                    "data_type": "uint16",
                    "scale": 10.0,
                    "tag": "LEVEL_SP",
                },
                {
                    "variable_id": "level_pct",
                    "protocol": "modbus",
                    "table": "input_registers",
                    "address": 0,
                    "access": "read",
                    "data_type": "uint16",
                    "scale": 10.0,
                    "tag": "LEVEL_PCT",
                },
                {
                    "variable_id": "pump_cmd",
                    "protocol": "modbus",
                    "table": "coils",
                    "address": 0,
                    "access": "read_write",
                    "data_type": "bool",
                    "tag": "PUMP_CMD",
                },
            ],
        }
    )
    register_map = ProcessRegisterMap(backend, scenario)
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )


def _modbus_step_from_request(
    *,
    step_id: str,
    request_hex: str,
    expected_protocol_status: ProtocolTransitionStatus,
    expected_reply_generated: bool | None,
    expected_world_patch_count: int | None,
    expected_process_values: Mapping[str, float] | None = None,
    expected_reply_values: tuple[int, ...] | None = None,
) -> BenchmarkStep:
    event = event_from_modbus_tcp_request(
        bytes.fromhex(request_hex),
        context=ModbusHookContext(
            session_id="benchmark-modbus",
            source_ip="192.0.2.10",
            actor_id="benchmark-actor",
            source_port=50200,
            destination_ip="192.0.2.20",
            destination_port=502,
        ),
    )
    return BenchmarkStep(
        step_id=step_id,
        request_hex=request_hex,
        event=event,
        expected_protocol_status=expected_protocol_status,
        expected_reply_generated=expected_reply_generated,
        expected_world_patch_count=expected_world_patch_count,
        expected_process_values=dict(expected_process_values or {}),
        expected_reply_values=expected_reply_values,
    )


def _manual_modbus_event(
    *,
    intent: Intent,
    operation: str,
    transaction_id: int,
    function_code: int,
    address: int | None,
    count: int | None,
    requested_value: object | None = None,
) -> ICSEvent:
    return ICSEvent(
        protocol="modbus",
        session_id="benchmark-modbus",
        source_ip="192.0.2.10",
        actor_id="benchmark-actor",
        source_port=50200,
        transaction_id=str(transaction_id),
        unit_id=1,
        intent=intent,
        operation=operation,
        address=address,
        count=count,
        requested_value=requested_value,
        result="observed",
        metadata={
            "function_code": function_code,
            "request_hex": _modbus_request_frame_hex(
                transaction_id=transaction_id,
                function_code=function_code,
                data=bytes.fromhex("00 00 00 01"),
            ),
        },
    )


def _modbus_read_holding_request(
    *,
    transaction_id: int,
    address: int,
    count: int,
) -> str:
    return _modbus_request_frame_hex(
        transaction_id=transaction_id,
        function_code=3,
        data=address.to_bytes(2, "big") + count.to_bytes(2, "big"),
    )


def _modbus_write_single_register_request(
    *,
    transaction_id: int,
    address: int,
    value: int,
) -> str:
    return _modbus_request_frame_hex(
        transaction_id=transaction_id,
        function_code=6,
        data=address.to_bytes(2, "big") + value.to_bytes(2, "big"),
    )


def _modbus_request_frame_hex(
    *,
    transaction_id: int,
    function_code: int,
    data: bytes,
    unit_id: int = 1,
) -> str:
    pdu = bytes([function_code]) + data
    length = 1 + len(pdu)
    raw = bytearray()
    raw.extend(transaction_id.to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend(length.to_bytes(2, "big"))
    raw.append(unit_id)
    raw.extend(pdu)
    return bytes(raw).hex()


def _validate_forced_reply(
    step: BenchmarkStep,
    observed_event: ICSEvent,
) -> tuple[bool | None, str | None]:
    if step.forced_reply_hex is None:
        return None, None
    reply = ProtocolReply(
        protocol="modbus_tcp",
        payload_hex=step.forced_reply_hex,
        transaction_id=observed_event.transaction_id,
        unit_id=observed_event.unit_id,
        reason="Benchmark forced generated reply validation.",
    )
    try:
        ProtocolReplyValidator().validate(reply, [observed_event])
    except ValueError as exc:
        return False, str(exc)
    return True, None


def _checks_for_step(
    step: BenchmarkStep,
    *,
    protocol_status: str,
    reply_generated: bool,
    world_patch_count: int,
    process_values: Mapping[str, float],
    reply_values: tuple[int, ...] | None,
    forced_reply_accepted: bool | None,
) -> dict[str, bool]:
    checks = {
        "protocol_status": protocol_status == step.expected_protocol_status.value,
    }
    if step.expected_reply_generated is not None:
        checks["reply_generated"] = reply_generated == step.expected_reply_generated
    if step.expected_world_patch_count is not None:
        checks["world_patch_count"] = (
            world_patch_count == step.expected_world_patch_count
        )
    for variable_id, expected_value in step.expected_process_values.items():
        actual_value = process_values.get(variable_id)
        checks[f"process_value:{variable_id}"] = (
            actual_value is not None
            and abs(float(actual_value) - float(expected_value)) <= 1e-9
        )
    if step.expected_reply_values is not None:
        checks["reply_values"] = reply_values == step.expected_reply_values
    if step.expected_forced_reply_accepted is not None:
        checks["forced_reply_accepted"] = (
            forced_reply_accepted == step.expected_forced_reply_accepted
        )
    return checks


def _read_process_values(
    context: PhysicalProcessContext | None,
    variable_ids: Any,
) -> dict[str, float]:
    if context is None:
        return {}
    values: dict[str, float] = {}
    for variable_id in variable_ids:
        values[str(variable_id)] = context.backend.read(str(variable_id))
    return values


def _reply_values(payload_hex: str) -> tuple[int, ...] | None:
    frame = parse_modbus_tcp_frame(payload_hex)
    if frame.is_exception or frame.function_code not in {3, 4}:
        return None
    byte_count = frame.data[0]
    values = frame.data[1 : 1 + byte_count]
    return tuple(
        int.from_bytes(values[offset : offset + 2], "big")
        for offset in range(0, len(values), 2)
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
