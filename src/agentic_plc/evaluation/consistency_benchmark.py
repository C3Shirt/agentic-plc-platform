from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from agentic_plc.adapters import (
    ModbusHookContext,
    event_from_modbus_tcp_request,
)
from agentic_plc.agent import AgentRuntime
from agentic_plc.agent.context_compressor import ProcessContextCompressor
from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.agent.protocol_state_machine import ProtocolTransitionStatus
from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.evaluation.physical_invariants import (
    InvariantComparison,
    ProcessInvariant,
    ProcessInvariantEvaluator,
    ProcessInvariantKind,
    ProcessInvariantResult,
    TrendDirection,
)
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.processes import (
    FormulaEquation,
    FormulaProcessBackend,
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
    expected_response_kind: str | None = None
    expected_exception_code: int | None = None
    process_invariants: tuple[ProcessInvariant, ...] = ()
    expected_snapshot_revision_delta: int | None = None
    expected_patch_base_revision_matches_before: bool | None = None
    expected_context_selected_variables: tuple[str, ...] = ()
    expected_actor_memory_event_count: int | None = None
    expected_actor_memory_touched_variables: tuple[str, ...] = ()
    tick_seconds_before: float = 0.0
    tick_seconds_after: float = 0.0
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
            "expected_response_kind": self.expected_response_kind,
            "expected_exception_code": self.expected_exception_code,
            "process_invariants": [
                invariant.to_dict() for invariant in self.process_invariants
            ],
            "expected_snapshot_revision_delta": self.expected_snapshot_revision_delta,
            "expected_patch_base_revision_matches_before": (
                self.expected_patch_base_revision_matches_before
            ),
            "expected_context_selected_variables": list(
                self.expected_context_selected_variables
            ),
            "expected_actor_memory_event_count": self.expected_actor_memory_event_count,
            "expected_actor_memory_touched_variables": list(
                self.expected_actor_memory_touched_variables
            ),
            "tick_seconds_before": self.tick_seconds_before,
            "tick_seconds_after": self.tick_seconds_after,
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
    snapshot_revision_before: int | None = None
    snapshot_revision_after: int | None = None
    snapshot_revision_delta: int | None = None
    patch_base_revisions: tuple[int | None, ...] = ()
    memory_result: BenchmarkMemoryResult | None = None
    process_invariant_results: tuple[ProcessInvariantResult, ...] = ()
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
            "snapshot_revision_before": self.snapshot_revision_before,
            "snapshot_revision_after": self.snapshot_revision_after,
            "snapshot_revision_delta": self.snapshot_revision_delta,
            "patch_base_revisions": list(self.patch_base_revisions),
            "memory_result": (
                self.memory_result.to_dict()
                if self.memory_result is not None
                else None
            ),
            "process_invariant_results": [
                result.to_dict() for result in self.process_invariant_results
            ],
            "forced_reply_accepted": self.forced_reply_accepted,
            "forced_reply_error": self.forced_reply_error,
            "checks": dict(self.checks),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkMemoryResult:
    """Observed memory/compression state for one benchmark step."""

    strategy: str | None
    selected_variables: tuple[str, ...] = ()
    selected_reason_codes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    actor_id: str | None = None
    actor_event_count: int | None = None
    touched_variables: tuple[str, ...] = ()
    recent_event_count: int = 0
    request_focus: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "selected_variables": list(self.selected_variables),
            "selected_reason_codes": {
                variable_id: list(reason_codes)
                for variable_id, reason_codes in self.selected_reason_codes.items()
            },
            "actor_id": self.actor_id,
            "actor_event_count": self.actor_event_count,
            "touched_variables": list(self.touched_variables),
            "recent_event_count": self.recent_event_count,
            "request_focus": (
                dict(self.request_focus) if self.request_focus is not None else None
            ),
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

    def check_group_counts(self) -> dict[str, dict[str, int]]:
        counts: dict[str, Counter[str]] = {}
        for results in self.cases.values():
            for result in results:
                for check_name, passed in result.checks.items():
                    group = _check_group(check_name)
                    group_counts = counts.setdefault(group, Counter())
                    group_counts["passed" if passed else "failed"] += 1
        return {
            group: dict(sorted(group_counts.items()))
            for group, group_counts in sorted(counts.items())
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "passed_steps": self.passed_steps,
            "total_steps": self.total_steps,
            "protocol_status_counts": self.status_counts(),
            "check_group_counts": self.check_group_counts(),
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
        invariant_evaluator: ProcessInvariantEvaluator | None = None,
        context_compressor: ProcessContextCompressor | None = None,
    ) -> None:
        self._process_context_factory = (
            process_context_factory or create_benchmark_process_context
        )
        self._invariant_evaluator = invariant_evaluator or ProcessInvariantEvaluator()
        self._context_compressor = context_compressor or ProcessContextCompressor()

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
            if context is not None and step.tick_seconds_before > 0:
                context.backend.tick(step.tick_seconds_before)
            before_snapshot = context.snapshot() if context is not None else None
            decision = runtime.observe_and_decide(step.event)
            if context is not None and step.tick_seconds_after > 0:
                context.backend.tick(step.tick_seconds_after)
            after_snapshot = context.snapshot() if context is not None else None
            observed_event = event_log.list_events()[-1]
            protocol_status = str(
                observed_event.metadata.get("protocol_fsm_status", "missing")
            )
            protocol_reason = observed_event.metadata.get("protocol_fsm_reason")
            reply_values = _reply_values(decision.protocol_replies[0].payload_hex) if decision.protocol_replies else None
            patch_base_revisions = _patch_base_revisions(decision.world_patches)
            memory_result = _memory_result(
                self._context_compressor,
                context,
                event_log.list_events(),
            )
            process_values = _read_process_values(
                context,
                step.expected_process_values.keys(),
            )
            forced_reply_accepted, forced_reply_error = _validate_forced_reply(
                step,
                observed_event,
            )
            process_invariant_results = self._invariant_evaluator.evaluate_many(
                step.process_invariants,
                context=context,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                event=observed_event,
                reply_values=reply_values,
            )
            checks = _checks_for_step(
                step,
                protocol_status=protocol_status,
                reply_generated=bool(decision.protocol_replies),
                world_patch_count=len(decision.world_patches),
                process_values=process_values,
                reply_values=reply_values,
                snapshot_revision_delta=_snapshot_revision_delta(
                    before_snapshot,
                    after_snapshot,
                ),
                patch_base_revisions=patch_base_revisions,
                before_snapshot_revision=(
                    before_snapshot.revision if before_snapshot is not None else None
                ),
                memory_result=memory_result,
                forced_reply_accepted=forced_reply_accepted,
                process_invariant_results=process_invariant_results,
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
                    snapshot_revision_before=(
                        before_snapshot.revision
                        if before_snapshot is not None
                        else None
                    ),
                    snapshot_revision_after=(
                        after_snapshot.revision
                        if after_snapshot is not None
                        else None
                    ),
                    snapshot_revision_delta=_snapshot_revision_delta(
                        before_snapshot,
                        after_snapshot,
                    ),
                    patch_base_revisions=patch_base_revisions,
                    memory_result=memory_result,
                    process_invariant_results=process_invariant_results,
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
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="level_sp_matches_decoded_write",
                            kind=ProcessInvariantKind.VARIABLE_EQUALS,
                            variable_id="level_sp",
                            value=70.0,
                            description=(
                                "The mapped setpoint must equal the engineering "
                                "value decoded from the Modbus write."
                            ),
                        ),
                        ProcessInvariant(
                            invariant_id="level_sp_within_declared_bounds",
                            kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                            variable_id="level_sp",
                            description=(
                                "The process backend should keep the setpoint "
                                "inside its declared engineering range."
                            ),
                        ),
                    ),
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
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="read_reply_matches_process_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Generated Modbus read values must be encoded "
                                "from the current physical-process snapshot."
                            ),
                        ),
                    ),
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
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="invalid_write_leaves_level_sp_stable",
                            kind=ProcessInvariantKind.TREND,
                            variable_id="level_sp",
                            direction=TrendDirection.STABLE,
                            description=(
                                "A physically invalid write should not mutate "
                                "the setpoint."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        BenchmarkCase(
            case_id="modbus_snapshot_memory_governance",
            description=(
                "A multi-step actor session should keep snapshot revisions, "
                "patch preconditions, touched PLC points, and compressed "
                "process context consistent across reads and writes."
            ),
            tags=(
                "protocol_fsm",
                "physical_consistency",
                "snapshot_consistency",
                "memory_consistency",
                "context_compression",
            ),
            steps=(
                _modbus_step_from_request(
                    step_id="read_initial_level_measurement",
                    request_hex=_modbus_read_input_request(
                        transaction_id=600,
                        address=0,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                    expected_reply_values=(480,),
                    expected_snapshot_revision_delta=0,
                    expected_context_selected_variables=("level_pct",),
                    expected_actor_memory_event_count=1,
                    expected_actor_memory_touched_variables=("level_pct",),
                ),
                _modbus_step_from_request(
                    step_id="write_setpoint_from_same_actor",
                    request_hex=_modbus_write_single_register_request(
                        transaction_id=601,
                        address=0,
                        value=650,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=1,
                    expected_process_values={"level_sp": 65.0},
                    expected_snapshot_revision_delta=1,
                    expected_patch_base_revision_matches_before=True,
                    expected_context_selected_variables=("level_sp",),
                    expected_actor_memory_event_count=2,
                    expected_actor_memory_touched_variables=(
                        "level_pct",
                        "level_sp",
                    ),
                ),
                _modbus_step_from_request(
                    step_id="write_pump_command_from_same_actor",
                    request_hex=_modbus_write_single_coil_request(
                        transaction_id=602,
                        address=0,
                        energized=False,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=1,
                    expected_process_values={"pump_cmd": 0.0},
                    expected_snapshot_revision_delta=1,
                    expected_patch_base_revision_matches_before=True,
                    expected_context_selected_variables=("pump_cmd",),
                    expected_actor_memory_event_count=3,
                    expected_actor_memory_touched_variables=(
                        "level_pct",
                        "level_sp",
                        "pump_cmd",
                    ),
                ),
                _modbus_step_from_request(
                    step_id="readback_setpoint_with_actor_memory",
                    request_hex=_modbus_read_holding_request(
                        transaction_id=603,
                        address=0,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                    expected_process_values={"level_sp": 65.0},
                    expected_reply_values=(650,),
                    expected_snapshot_revision_delta=0,
                    expected_context_selected_variables=("level_sp",),
                    expected_actor_memory_event_count=4,
                    expected_actor_memory_touched_variables=(
                        "level_pct",
                        "level_sp",
                        "pump_cmd",
                    ),
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


def create_formula_benchmark_process_context() -> PhysicalProcessContext:
    """Create an equation-driven process backend for dynamic consistency tests."""

    backend = FormulaProcessBackend(
        process_id="formula_tank_process",
        name="formula_tank",
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
        initial_state={
            "level_sp": 50.0,
            "level_pct": 48.0,
            "pump_cmd": 1.0,
        },
        equations=[
            FormulaEquation(
                target="level_pct",
                expression=(
                    "level_pct + dt * "
                    "(0.2 * (level_sp - level_pct) + 1.5 * pump_cmd)"
                ),
                description=(
                    "First-order attacker-observable level response driven by "
                    "setpoint error and pump command."
                ),
            )
        ],
        metadata={"simulator_family": "formula_tank"},
    )
    scenario = ScenarioMapping.from_file("scenarios/formula_tank/scenario.json")
    register_map = ProcessRegisterMap(backend, scenario)
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )


def build_formula_process_consistency_cases() -> tuple[BenchmarkCase, ...]:
    """Build dynamic-process cases for equation-driven backend validation."""

    return (
        BenchmarkCase(
            case_id="formula_process_write_then_dynamics",
            description=(
                "A formula-driven process backend should accept a setpoint "
                "write, evolve the measurement toward the new target, and "
                "serve a readback encoded from the evolved snapshot."
            ),
            tags=(
                "formula_process",
                "physical_consistency",
                "snapshot_consistency",
                "generated_reply_gate",
            ),
            steps=(
                _modbus_step_from_request(
                    step_id="write_level_setpoint_then_tick",
                    request_hex=_modbus_write_single_register_request(
                        transaction_id=700,
                        address=0,
                        value=700,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=1,
                    expected_process_values={
                        "level_sp": 70.0,
                        "level_pct": 53.9,
                    },
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="formula_level_moves_toward_setpoint",
                            kind=ProcessInvariantKind.MOVES_TOWARD,
                            variable_id="level_pct",
                            target_variable="level_sp",
                            tolerance=1e-6,
                            description=(
                                "After a setpoint write and one formula tick, "
                                "the attacker-visible level should move toward "
                                "the new setpoint."
                            ),
                        ),
                        ProcessInvariant(
                            invariant_id="formula_level_stays_bounded",
                            kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                            variable_id="level_pct",
                            description=(
                                "Formula dynamics must keep the level inside "
                                "declared engineering bounds."
                            ),
                        ),
                        ProcessInvariant(
                            invariant_id="formula_setpoint_above_level_after_tick",
                            kind=ProcessInvariantKind.RELATION,
                            left_variable="level_sp",
                            right_variable="level_pct",
                            operator=InvariantComparison.GT,
                            tolerance=1e-6,
                            description=(
                                "One tick should move the level upward without "
                                "instantaneously jumping to the setpoint."
                            ),
                        ),
                    ),
                    expected_snapshot_revision_delta=2,
                    expected_patch_base_revision_matches_before=True,
                    tick_seconds_after=1.0,
                ),
                _modbus_step_from_request(
                    step_id="read_formula_level_after_dynamics",
                    request_hex=_modbus_read_input_request(
                        transaction_id=701,
                        address=0,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                    expected_reply_generated=True,
                    expected_world_patch_count=0,
                    expected_process_values={"level_pct": 53.9},
                    expected_reply_values=(539,),
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="formula_reply_matches_evolved_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "The generated read reply must encode the "
                                "formula-evolved snapshot value."
                            ),
                        ),
                    ),
                    expected_snapshot_revision_delta=0,
                ),
            ),
        ),
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
    process_invariants: tuple[ProcessInvariant, ...] = (),
    expected_snapshot_revision_delta: int | None = None,
    expected_patch_base_revision_matches_before: bool | None = None,
    expected_context_selected_variables: tuple[str, ...] = (),
    expected_actor_memory_event_count: int | None = None,
    expected_actor_memory_touched_variables: tuple[str, ...] = (),
    tick_seconds_before: float = 0.0,
    tick_seconds_after: float = 0.0,
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
        process_invariants=process_invariants,
        expected_snapshot_revision_delta=expected_snapshot_revision_delta,
        expected_patch_base_revision_matches_before=(
            expected_patch_base_revision_matches_before
        ),
        expected_context_selected_variables=expected_context_selected_variables,
        expected_actor_memory_event_count=expected_actor_memory_event_count,
        expected_actor_memory_touched_variables=(
            expected_actor_memory_touched_variables
        ),
        tick_seconds_before=tick_seconds_before,
        tick_seconds_after=tick_seconds_after,
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


def _modbus_read_input_request(
    *,
    transaction_id: int,
    address: int,
    count: int,
) -> str:
    return _modbus_request_frame_hex(
        transaction_id=transaction_id,
        function_code=4,
        data=address.to_bytes(2, "big") + count.to_bytes(2, "big"),
    )


def _modbus_write_single_coil_request(
    *,
    transaction_id: int,
    address: int,
    energized: bool,
) -> str:
    value = 0xFF00 if energized else 0x0000
    return _modbus_request_frame_hex(
        transaction_id=transaction_id,
        function_code=5,
        data=address.to_bytes(2, "big") + value.to_bytes(2, "big"),
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
    snapshot_revision_delta: int | None,
    patch_base_revisions: tuple[int | None, ...],
    before_snapshot_revision: int | None,
    memory_result: BenchmarkMemoryResult | None,
    forced_reply_accepted: bool | None,
    process_invariant_results: tuple[ProcessInvariantResult, ...],
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
    if step.expected_snapshot_revision_delta is not None:
        checks["snapshot_revision_delta"] = (
            snapshot_revision_delta == step.expected_snapshot_revision_delta
        )
    if step.expected_patch_base_revision_matches_before is not None:
        matches = (
            before_snapshot_revision is not None
            and patch_base_revisions
            and all(
                revision == before_snapshot_revision
                for revision in patch_base_revisions
            )
        )
        checks["patch_base_revision_matches_before"] = (
            bool(matches)
            == step.expected_patch_base_revision_matches_before
        )
    if step.expected_context_selected_variables:
        selected = (
            set(memory_result.selected_variables)
            if memory_result is not None
            else set()
        )
        checks["memory_context_selected_variables"] = set(
            step.expected_context_selected_variables
        ).issubset(selected)
    if step.expected_actor_memory_event_count is not None:
        checks["memory_actor_event_count"] = (
            memory_result is not None
            and memory_result.actor_event_count
            == step.expected_actor_memory_event_count
        )
    if step.expected_actor_memory_touched_variables:
        touched = (
            set(memory_result.touched_variables)
            if memory_result is not None
            else set()
        )
        checks["memory_touched_variables"] = set(
            step.expected_actor_memory_touched_variables
        ).issubset(touched)
    if step.expected_forced_reply_accepted is not None:
        checks["forced_reply_accepted"] = (
            forced_reply_accepted == step.expected_forced_reply_accepted
        )
    for result in process_invariant_results:
        checks[f"process_invariant:{result.invariant_id}"] = result.passed
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


def _snapshot_revision_delta(
    before_snapshot: Any,
    after_snapshot: Any,
) -> int | None:
    if before_snapshot is None or after_snapshot is None:
        return None
    return int(after_snapshot.revision) - int(before_snapshot.revision)


def _patch_base_revisions(applied_patches: Any) -> tuple[int | None, ...]:
    revisions: list[int | None] = []
    for applied_patch in applied_patches:
        metadata = getattr(getattr(applied_patch, "patch", None), "metadata", {})
        value = metadata.get("base_revision") if isinstance(metadata, Mapping) else None
        revisions.append(None if value is None else int(value))
    return tuple(revisions)


def _memory_result(
    compressor: ProcessContextCompressor,
    context: PhysicalProcessContext | None,
    events: list[ICSEvent],
) -> BenchmarkMemoryResult | None:
    if context is None or not events:
        return None
    compressed = compressor.compress(context, events)
    actor_memory = compressed.actor_memory
    return BenchmarkMemoryResult(
        strategy=compressed.compression.get("strategy"),
        selected_variables=tuple(
            point.variable_id for point in compressed.exposed_points
        ),
        selected_reason_codes={
            point.variable_id: tuple(point.reason_codes)
            for point in compressed.exposed_points
        },
        actor_id=actor_memory.actor_id if actor_memory is not None else None,
        actor_event_count=(
            actor_memory.event_count if actor_memory is not None else None
        ),
        touched_variables=(
            tuple(point.variable_id for point in actor_memory.touched_points)
            if actor_memory is not None
            else ()
        ),
        recent_event_count=(
            len(actor_memory.recent_events) if actor_memory is not None else 0
        ),
        request_focus=compressed.request_focus,
    )


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


def _check_group(check_name: str) -> str:
    if check_name.startswith("protocol_"):
        return "protocol_state"
    if check_name.startswith("snapshot_") or check_name.startswith("patch_base_"):
        return "snapshot_consistency"
    if check_name.startswith("memory_"):
        return "memory_consistency"
    if check_name.startswith("process_"):
        return "physical_process"
    if check_name in {
        "reply_generated",
        "reply_values",
        "forced_reply_accepted",
        "world_patch_count",
    }:
        return "generated_action"
    return "other"
