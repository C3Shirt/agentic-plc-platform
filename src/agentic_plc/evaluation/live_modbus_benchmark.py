from __future__ import annotations

import json
import socket
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from agentic_plc.adapters import ModbusHookContext, event_from_modbus_tcp_request
from agentic_plc.contracts.actions import ProtocolReply
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.agent.protocol_state_machine import ProtocolTransitionStatus
from agentic_plc.evaluation.consistency_benchmark import (
    BenchmarkCase,
    BenchmarkStep,
)
from agentic_plc.evaluation.physical_invariants import (
    InvariantComparison,
    ProcessInvariant,
    ProcessInvariantKind,
    ProcessInvariantResult,
    TrendDirection,
)
from agentic_plc.evaluation.modbus_scenario import modbus_points_from_scenario
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    ModbusTcpFrame,
    build_modbus_tcp_read_request,
    build_modbus_tcp_write_multiple_coils_request,
    build_modbus_tcp_write_multiple_registers_request,
    build_modbus_tcp_write_single_coil_request,
    build_modbus_tcp_write_single_register_request,
    parse_modbus_tcp_frame,
)


DEFAULT_HMI_VARIABLE_ALIASES: dict[str, str] = {
    "level_sp": "level_setpoint_percent",
    "level_pct": "level_percent",
    "pump_cmd": "outlet_pump_running",
}

DEFAULT_HMI_VARIABLE_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "level_sp": (0.0, 100.0),
    "level_setpoint_percent": (0.0, 100.0),
    "level_pct": (0.0, 100.0),
    "level_percent": (0.0, 100.0),
    "pressure_bar": (0.0, None),
    "pump_cmd": (0.0, 1.0),
    "outlet_pump_running": (0.0, 1.0),
    "inlet_valve_open": (0.0, 1.0),
    "high_level_alarm": (0.0, 1.0),
}

TANK_PUMP_MODE_CODES: dict[str, int] = {
    "stop": 0,
    "manual": 1,
    "auto": 2,
    "fault": 3,
}


@dataclass(frozen=True, slots=True)
class HMIRegisterBinding:
    """Maps a Modbus read cell to a state field exposed by an HMI endpoint."""

    function_code: int
    address: int
    state_key: str
    scale: float = 1.0
    value_map: Mapping[str, int] = field(default_factory=dict)

    def encode(self, state: Mapping[str, Any]) -> int | None:
        if self.state_key not in state:
            return None
        value = state[self.state_key]
        if self.value_map:
            mapped = self.value_map.get(str(value).lower())
            return int(mapped) if mapped is not None else None
        if isinstance(value, bool):
            return int(value)
        return int(round(float(value) * self.scale))


DEFAULT_HMI_REGISTER_BINDINGS: tuple[HMIRegisterBinding, ...] = (
    HMIRegisterBinding(1, 0, "outlet_pump_running"),
    HMIRegisterBinding(1, 1, "inlet_valve_open"),
    HMIRegisterBinding(2, 0, "high_level_alarm"),
    HMIRegisterBinding(3, 0, "level_setpoint_percent", scale=10.0),
    HMIRegisterBinding(3, 1, "mode", value_map=TANK_PUMP_MODE_CODES),
    HMIRegisterBinding(4, 0, "level_percent", scale=10.0),
    HMIRegisterBinding(4, 1, "pressure_bar", scale=100.0),
)


def hmi_register_bindings_from_scenario(
    scenario: str | Path | Mapping[str, Any] | object,
) -> tuple[HMIRegisterBinding, ...]:
    """Build HMI reply-snapshot bindings from a Modbus process scenario.

    The default assumption is that the HMI state endpoint exposes canonical
    process variable IDs. Tank-pump's built-in observer keeps its hand-written
    aliases; scenario-driven TE or future backends can use this helper instead.
    """

    return tuple(
        HMIRegisterBinding(
            point.function_code,
            point.address,
            point.variable_id,
            scale=point.scale,
        )
        for point in modbus_points_from_scenario(scenario)
    )


def build_default_live_modbus_cases() -> tuple[BenchmarkCase, ...]:
    """Build live black-box Modbus cases for the tank-pump honeypot surface.

    These cases reuse the public benchmark schema but focus on what can be
    observed through a real Modbus TCP socket plus an optional HMI/event-log
    side channel. Stable process points use exact read-back checks; dynamic
    measurements use bounds to avoid false failures from normal simulation
    ticks between a Modbus response and the HMI snapshot.
    """

    return (
        BenchmarkCase(
            case_id="live_modbus_table_scan",
            description=(
                "Scan every exposed Modbus table and, when an HMI observer is "
                "available, compare stable table values with the live process "
                "snapshot."
            ),
            requires_process_context=False,
            tags=("live", "modbus", "scan", "multi_table", "physical_observer"),
            steps=(
                _live_modbus_step_from_request(
                    step_id="read_coils_0_2",
                    request_hex=_modbus_read_request(
                        transaction_id=600,
                        function_code=1,
                        address=0,
                        count=2,
                    ),
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="coil_values_match_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Coil read-back should match HMI actuator state."
                            ),
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_discrete_inputs_0_1",
                    request_hex=_modbus_read_request(
                        transaction_id=601,
                        function_code=2,
                        address=0,
                        count=1,
                    ),
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="discrete_input_matches_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Discrete-input alarm state should match HMI state."
                            ),
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_input_registers_0_2",
                    request_hex=_modbus_read_request(
                        transaction_id=602,
                        function_code=4,
                        address=0,
                        count=2,
                    ),
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="level_measurement_within_bounds",
                            kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                            variable_id="level_pct",
                            description="Live level measurement should stay bounded.",
                        ),
                        ProcessInvariant(
                            invariant_id="pressure_measurement_nonnegative",
                            kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                            variable_id="pressure_bar",
                            description="Live pressure should stay non-negative.",
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_holding_registers_0_2",
                    request_hex=_modbus_read_request(
                        transaction_id=603,
                        function_code=3,
                        address=0,
                        count=2,
                    ),
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="holding_registers_match_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Stable setpoint/mode registers should match HMI."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        BenchmarkCase(
            case_id="live_modbus_single_register_write_readback",
            description=(
                "Write a level setpoint through function 6 and verify that both "
                "HMI state and a subsequent holding-register read reflect it."
            ),
            requires_process_context=False,
            tags=("live", "modbus", "write_readback", "holding_registers"),
            steps=(
                _live_modbus_step_from_request(
                    step_id="write_level_setpoint_70_percent",
                    request_hex=_modbus_write_single_register_request(
                        transaction_id=610,
                        address=0,
                        value=700,
                    ),
                    expected_process_values={"level_sp": 70.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="hmi_level_sp_is_70_after_single_write",
                            kind=ProcessInvariantKind.VARIABLE_EQUALS,
                            variable_id="level_sp",
                            value=70.0,
                            description=(
                                "The HMI-observed setpoint should decode to 70%."
                            ),
                        ),
                        ProcessInvariant(
                            invariant_id="hmi_level_sp_stays_in_bounds",
                            kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                            variable_id="level_sp",
                            description="The setpoint should remain in process bounds.",
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_level_setpoint_after_single_write",
                    request_hex=_modbus_read_request(
                        transaction_id=611,
                        function_code=3,
                        address=0,
                        count=1,
                    ),
                    expected_reply_values=(700,),
                    expected_process_values={"level_sp": 70.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="single_write_readback_matches_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Setpoint read-back should match the HMI snapshot."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        BenchmarkCase(
            case_id="live_modbus_coil_control_readback",
            description=(
                "Toggle the outlet-pump coil and verify coil read-back plus HMI "
                "actuator state."
            ),
            requires_process_context=False,
            tags=("live", "modbus", "write_readback", "coils", "actuator"),
            steps=(
                _live_modbus_step_from_request(
                    step_id="force_outlet_pump_on",
                    request_hex=_modbus_write_single_coil_request(
                        transaction_id=620,
                        address=0,
                        energized=True,
                    ),
                    expected_process_values={"pump_cmd": 1.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="hmi_pump_on_after_coil_write",
                            kind=ProcessInvariantKind.VARIABLE_EQUALS,
                            variable_id="pump_cmd",
                            value=1.0,
                            description="Coil write should turn the pump on.",
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_outlet_pump_coil_on",
                    request_hex=_modbus_read_request(
                        transaction_id=621,
                        function_code=1,
                        address=0,
                        count=1,
                    ),
                    expected_reply_values=(1,),
                    expected_process_values={"pump_cmd": 1.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="pump_on_readback_matches_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Pump coil read-back should match the HMI state."
                            ),
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="force_outlet_pump_off",
                    request_hex=_modbus_write_single_coil_request(
                        transaction_id=622,
                        address=0,
                        energized=False,
                    ),
                    expected_process_values={"pump_cmd": 0.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="hmi_pump_off_after_coil_write",
                            kind=ProcessInvariantKind.VARIABLE_EQUALS,
                            variable_id="pump_cmd",
                            value=0.0,
                            description="Coil write should turn the pump off.",
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_outlet_pump_coil_off",
                    request_hex=_modbus_read_request(
                        transaction_id=623,
                        function_code=1,
                        address=0,
                        count=1,
                    ),
                    expected_reply_values=(0,),
                    expected_process_values={"pump_cmd": 0.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="pump_off_readback_matches_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Pump-off coil read-back should match the HMI state."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        BenchmarkCase(
            case_id="live_modbus_multiple_write_readback",
            description=(
                "Use function 16 and 15 batch writes, then verify register/coil "
                "read-back through the live protocol endpoint."
            ),
            requires_process_context=False,
            tags=("live", "modbus", "multiple_write", "write_readback"),
            steps=(
                _live_modbus_step_from_request(
                    step_id="write_setpoint_and_mode_batch",
                    request_hex=_modbus_write_multiple_registers_request(
                        transaction_id=630,
                        address=0,
                        values=(650, 2),
                    ),
                    expected_process_values={"level_sp": 65.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="hmi_level_sp_is_65_after_batch_write",
                            kind=ProcessInvariantKind.VARIABLE_EQUALS,
                            variable_id="level_sp",
                            value=65.0,
                            description=(
                                "Batch register write should set the level setpoint."
                            ),
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="read_setpoint_and_mode_after_batch",
                    request_hex=_modbus_read_request(
                        transaction_id=631,
                        function_code=3,
                        address=0,
                        count=2,
                    ),
                    expected_reply_values=(650, 2),
                    expected_process_values={"level_sp": 65.0},
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="batch_register_readback_matches_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Batch-written registers should match HMI state."
                            ),
                        ),
                    ),
                ),
                _live_modbus_step_from_request(
                    step_id="write_pump_and_inlet_coils_batch",
                    request_hex=_modbus_write_multiple_coils_request(
                        transaction_id=632,
                        address=0,
                        values=(1, 1),
                    ),
                    expected_process_values={
                        "pump_cmd": 1.0,
                        "inlet_valve_open": 1.0,
                    },
                ),
                _live_modbus_step_from_request(
                    step_id="read_pump_and_inlet_coils_after_batch",
                    request_hex=_modbus_read_request(
                        transaction_id=633,
                        function_code=1,
                        address=0,
                        count=2,
                    ),
                    expected_reply_values=(1, 1),
                    expected_process_values={
                        "pump_cmd": 1.0,
                        "inlet_valve_open": 1.0,
                    },
                    process_invariants=(
                        ProcessInvariant(
                            invariant_id="batch_coil_readback_matches_hmi_snapshot",
                            kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
                            description=(
                                "Batch-written coils should match HMI state."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        BenchmarkCase(
            case_id="live_modbus_exception_probing",
            description=(
                "Probe unmapped but syntactically valid Modbus addresses and "
                "require normal Modbus exception responses rather than malformed "
                "frames or silent state corruption."
            ),
            requires_process_context=False,
            tags=("live", "modbus", "exception", "invalid_address", "probing"),
            steps=(
                _live_modbus_step_from_request(
                    step_id="read_unmapped_holding_register",
                    request_hex=_modbus_read_request(
                        transaction_id=640,
                        function_code=3,
                        address=99,
                        count=1,
                    ),
                    expected_response_kind="exception",
                    expected_exception_code=2,
                    notes="Exception code 2 is Illegal Data Address.",
                ),
                _live_modbus_step_from_request(
                    step_id="read_unmapped_coil",
                    request_hex=_modbus_read_request(
                        transaction_id=641,
                        function_code=1,
                        address=99,
                        count=1,
                    ),
                    expected_response_kind="exception",
                    expected_exception_code=2,
                    notes="Exception code 2 is Illegal Data Address.",
                ),
            ),
        ),
        BenchmarkCase(
            case_id="live_modbus_transaction_reuse_sequence",
            description=(
                "Reuse a transaction id for a different request within one live "
                "case. The TCP response may still be well formed, but event-log "
                "telemetry should mark the second request anomalous when session "
                "correlation is enabled."
            ),
            requires_process_context=False,
            tags=("live", "modbus", "protocol_fsm", "transaction_id", "anomaly"),
            steps=(
                _live_modbus_step_from_request(
                    step_id="transaction_reuse_baseline_read",
                    request_hex=_modbus_read_request(
                        transaction_id=650,
                        function_code=3,
                        address=0,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ALLOWED,
                ),
                _live_modbus_step_from_request(
                    step_id="transaction_reuse_different_address",
                    request_hex=_modbus_read_request(
                        transaction_id=650,
                        function_code=3,
                        address=1,
                        count=1,
                    ),
                    expected_protocol_status=ProtocolTransitionStatus.ANOMALOUS,
                ),
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class HMIStateObserver:
    """Black-box process observer backed by a JSON HMI state endpoint.

    The endpoint may either return the process state directly or use the local
    HMI shape: ``{"scenario": "...", "state": {...}}``. The aliases and
    register bindings keep the live runner generic enough for future physical
    processes: a new simulator only needs to provide a JSON state endpoint plus
    a mapping from benchmark variable/register names to endpoint fields.
    """

    state_url: str
    timeout_seconds: float = 2.0
    variable_aliases: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_HMI_VARIABLE_ALIASES)
    )
    variable_bounds: Mapping[str, tuple[float | None, float | None]] = field(
        default_factory=lambda: dict(DEFAULT_HMI_VARIABLE_BOUNDS)
    )
    register_bindings: tuple[HMIRegisterBinding, ...] = DEFAULT_HMI_REGISTER_BINDINGS

    def snapshot(self) -> Mapping[str, Any]:
        with urllib.request.urlopen(
            self.state_url,
            timeout=self.timeout_seconds,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, Mapping) and isinstance(payload.get("state"), Mapping):
            return dict(payload["state"])
        if isinstance(payload, Mapping):
            return dict(payload)
        raise ValueError("HMI state endpoint must return a JSON object")

    def variable_value(
        self,
        state: Mapping[str, Any] | None,
        variable_id: str,
    ) -> float | None:
        if state is None:
            return None
        state_key = self.variable_aliases.get(variable_id, variable_id)
        if state_key not in state:
            return None
        value = state[state_key]
        if isinstance(value, bool):
            return float(int(value))
        return float(value)

    def bounds_for(self, variable_id: str) -> tuple[float | None, float | None]:
        state_key = self.variable_aliases.get(variable_id, variable_id)
        return self.variable_bounds.get(
            variable_id,
            self.variable_bounds.get(state_key, (None, None)),
        )

    def register_values_for_event(
        self,
        event: ICSEvent,
        state: Mapping[str, Any] | None,
    ) -> tuple[int, ...] | None:
        if state is None or event.address is None or event.count is None:
            return None
        function_code = _event_function_code(event)
        if function_code not in {1, 2, 3, 4}:
            return None

        values: list[int] = []
        for address in range(int(event.address), int(event.address) + int(event.count)):
            binding = self._binding_for(function_code, address)
            if binding is None:
                return None
            value = binding.encode(state)
            if value is None:
                return None
            values.append(value)
        return tuple(values)

    def _binding_for(
        self,
        function_code: int,
        address: int,
    ) -> HMIRegisterBinding | None:
        for binding in self.register_bindings:
            if binding.function_code == function_code and binding.address == address:
                return binding
        return None


@dataclass(frozen=True, slots=True)
class LiveBenchmarkStepResult:
    step_id: str
    request_hex: str | None
    response_hex: str | None = None
    response_error: str | None = None
    latency_ms: float | None = None
    response_function_code: int | None = None
    response_is_exception: bool | None = None
    exception_code: int | None = None
    response_valid: bool = False
    response_validation_error: str | None = None
    protocol_status: str | None = None
    protocol_reason: str | None = None
    reply_values: tuple[int, ...] | None = None
    hmi_state_before: Mapping[str, Any] | None = None
    hmi_state_after: Mapping[str, Any] | None = None
    process_values: Mapping[str, float] = field(default_factory=dict)
    process_invariant_results: tuple[ProcessInvariantResult, ...] = ()
    checks: Mapping[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "request_hex": self.request_hex,
            "response_hex": self.response_hex,
            "response_error": self.response_error,
            "latency_ms": self.latency_ms,
            "response_function_code": self.response_function_code,
            "response_is_exception": self.response_is_exception,
            "exception_code": self.exception_code,
            "response_valid": self.response_valid,
            "response_validation_error": self.response_validation_error,
            "protocol_status": self.protocol_status,
            "protocol_reason": self.protocol_reason,
            "reply_values": (
                list(self.reply_values) if self.reply_values is not None else None
            ),
            "hmi_state_before": (
                dict(self.hmi_state_before)
                if self.hmi_state_before is not None
                else None
            ),
            "hmi_state_after": (
                dict(self.hmi_state_after)
                if self.hmi_state_after is not None
                else None
            ),
            "process_values": dict(self.process_values),
            "process_invariant_results": [
                result.to_dict() for result in self.process_invariant_results
            ],
            "checks": dict(self.checks),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class LiveBenchmarkReport:
    cases: Mapping[str, tuple[LiveBenchmarkStepResult, ...]]

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
                if result.protocol_status is None:
                    counts["unobserved"] += 1
                else:
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


class LiveModbusBenchmarkRunner:
    """Run benchmark cases against a real Modbus TCP endpoint.

    This runner is deliberately black-box at the protocol boundary. It sends the
    same request bytes that an attacker or scanner would send, then validates the
    returned Modbus frame and optional HMI-observed physical state. If the local
    JSONL event log is supplied, it also checks the protocol state machine status
    that the honeypot recorded for the same request.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 1502,
        timeout_seconds: float = 2.0,
        hmi_observer: HMIStateObserver | None = None,
        event_log_path: Path | str | None = None,
        event_log_timeout_seconds: float = 1.0,
        reuse_connection_per_case: bool = True,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._timeout_seconds = float(timeout_seconds)
        self._hmi_observer = hmi_observer
        self._event_log_path = Path(event_log_path) if event_log_path else None
        self._event_log_timeout_seconds = float(event_log_timeout_seconds)
        self._reuse_connection_per_case = reuse_connection_per_case

    def run(
        self,
        cases: tuple[BenchmarkCase, ...] | None = None,
    ) -> LiveBenchmarkReport:
        cases = cases or build_default_live_modbus_cases()
        return LiveBenchmarkReport(
            cases={case.case_id: self.run_case(case) for case in cases}
        )

    def run_case(self, case: BenchmarkCase) -> tuple[LiveBenchmarkStepResult, ...]:
        results: list[LiveBenchmarkStepResult] = []
        if self._reuse_connection_per_case:
            clients: dict[str, ModbusTcpClient] = {}
            try:
                for step in case.steps:
                    if step.tick_seconds_before > 0:
                        time.sleep(step.tick_seconds_before)
                    session_id = step.event.session_id or case.case_id
                    client = clients.get(session_id)
                    if client is None:
                        client = ModbusTcpClient(
                            self._host,
                            self._port,
                            timeout_seconds=self._timeout_seconds,
                        )
                        client.connect()
                        clients[session_id] = client
                    results.append(self.run_step(step, client=client))
            finally:
                for client in clients.values():
                    client.close()
            return tuple(results)

        for step in case.steps:
            if step.tick_seconds_before > 0:
                time.sleep(step.tick_seconds_before)
            results.append(self.run_step(step, client=None))
        return tuple(results)

    def run_step(
        self,
        step: BenchmarkStep,
        *,
        client: "ModbusTcpClient | None" = None,
    ) -> LiveBenchmarkStepResult:
        before_state = _safe_hmi_snapshot(self._hmi_observer)
        response_hex: str | None = None
        response_error: str | None = None
        response_valid = False
        response_validation_error: str | None = None
        reply_values: tuple[int, ...] | None = None
        latency_ms: float | None = None
        response_function_code: int | None = None
        response_is_exception: bool | None = None
        exception_code: int | None = None

        if step.request_hex is None:
            response_error = "benchmark step has no request_hex"
        else:
            started = time.perf_counter()
            try:
                if client is not None:
                    response_hex = client.request(step.request_hex)
                else:
                    response_hex = send_modbus_tcp_request(
                        self._host,
                        self._port,
                        step.request_hex,
                        timeout_seconds=self._timeout_seconds,
                    )
                latency_ms = (time.perf_counter() - started) * 1000
            except Exception as exc:
                response_error = str(exc)
                latency_ms = (time.perf_counter() - started) * 1000

        if response_hex is not None:
            try:
                frame = parse_modbus_tcp_frame(response_hex)
                response_valid = True
                response_function_code = (
                    frame.function_code & 0x7F
                    if frame.is_exception
                    else frame.function_code
                )
                response_is_exception = frame.is_exception
                exception_code = frame.data[0] if frame.is_exception else None
                reply_values = decode_modbus_response_values(
                    frame,
                    count=step.event.count,
                )
                _validate_response_against_step(response_hex, step)
            except (ValueError, ModbusFrameError) as exc:
                response_validation_error = str(exc)

        after_state = _safe_hmi_snapshot(self._hmi_observer)
        observed_event = self._find_event_for_request(step.request_hex)
        protocol_status = None
        protocol_reason = None
        if observed_event is not None:
            protocol_status = _optional_text(
                observed_event.get("metadata", {}).get("protocol_fsm_status")
            )
            protocol_reason = _optional_text(
                observed_event.get("metadata", {}).get("protocol_fsm_reason")
            )

        process_values = _read_process_values(
            self._hmi_observer,
            after_state,
            step.expected_process_values.keys(),
        )
        invariant_results = (
            _evaluate_live_invariants(
                step.process_invariants,
                observer=self._hmi_observer,
                before_state=before_state,
                after_state=after_state,
                event=step.event,
                reply_values=reply_values,
            )
            if self._hmi_observer is not None
            else ()
        )
        checks = _checks_for_live_step(
            step,
            response_hex=response_hex,
            response_error=response_error,
            response_valid=response_valid,
            response_validation_error=response_validation_error,
            response_is_exception=response_is_exception,
            exception_code=exception_code,
            protocol_status=protocol_status,
            event_log_path=self._event_log_path,
            hmi_observer_configured=self._hmi_observer is not None,
            reply_values=reply_values,
            process_values=process_values,
            process_invariant_results=invariant_results,
        )
        return LiveBenchmarkStepResult(
            step_id=step.step_id,
            request_hex=step.request_hex,
            response_hex=response_hex,
            response_error=response_error,
            latency_ms=latency_ms,
            response_function_code=response_function_code,
            response_is_exception=response_is_exception,
            exception_code=exception_code,
            response_valid=response_valid,
            response_validation_error=response_validation_error,
            protocol_status=protocol_status,
            protocol_reason=protocol_reason,
            reply_values=reply_values,
            hmi_state_before=before_state,
            hmi_state_after=after_state,
            process_values=process_values,
            process_invariant_results=invariant_results,
            checks=checks,
        )

    def _find_event_for_request(self, request_hex: str | None) -> Mapping[str, Any] | None:
        if self._event_log_path is None or request_hex is None:
            return None
        deadline = time.perf_counter() + self._event_log_timeout_seconds
        normalized = _clean_hex(request_hex)
        while True:
            event = _latest_event_with_request_hex(self._event_log_path, normalized)
            if event is not None:
                return event
            if time.perf_counter() >= deadline:
                return None
            time.sleep(0.05)


class ModbusTcpClient:
    """Small persistent Modbus TCP client for stateful live benchmark cases."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._timeout_seconds = float(timeout_seconds)
        self._sock: socket.socket | None = None

    def __enter__(self) -> "ModbusTcpClient":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connect(self) -> None:
        self.close()
        self._sock = socket.create_connection(
            (self._host, self._port),
            timeout=self._timeout_seconds,
        )
        self._sock.settimeout(self._timeout_seconds)

    def request(self, request_hex: str) -> str:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        request = bytes.fromhex(_clean_hex(request_hex))
        self._sock.sendall(request)
        header = _recv_exact(self._sock, 7)
        length = int.from_bytes(header[4:6], "big")
        if length <= 0:
            raise ModbusFrameError("Modbus TCP response length must be positive")
        body = _recv_exact(self._sock, length - 1)
        return (header + body).hex()

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        finally:
            self._sock = None


def send_modbus_tcp_request(
    host: str,
    port: int,
    request_hex: str,
    *,
    timeout_seconds: float = 2.0,
) -> str:
    """Send one Modbus TCP ADU and return exactly one response ADU as hex."""

    with ModbusTcpClient(
        host,
        port,
        timeout_seconds=timeout_seconds,
    ) as client:
        return client.request(request_hex)


def decode_modbus_response_values(
    frame: ModbusTcpFrame | str,
    *,
    count: int | None = None,
) -> tuple[int, ...] | None:
    parsed = parse_modbus_tcp_frame(frame) if isinstance(frame, str) else frame
    if parsed.is_exception:
        return None
    if parsed.function_code in {3, 4}:
        byte_count = parsed.data[0]
        values = parsed.data[1 : 1 + byte_count]
        return tuple(
            int.from_bytes(values[offset : offset + 2], "big")
            for offset in range(0, len(values), 2)
        )
    if parsed.function_code in {1, 2}:
        byte_count = parsed.data[0]
        packed = parsed.data[1 : 1 + byte_count]
        values: list[int] = []
        for byte in packed:
            for bit in range(8):
                values.append((byte >> bit) & 1)
        if count is not None:
            values = values[: int(count)]
        return tuple(values)
    return None


def _validate_response_against_step(response_hex: str, step: BenchmarkStep) -> None:
    reply = ProtocolReply(
        protocol="modbus_tcp",
        payload_hex=response_hex,
        transaction_id=step.event.transaction_id,
        unit_id=step.event.unit_id,
        reason="Live benchmark observed Modbus response.",
    )
    ProtocolReplyValidator().validate(reply, [step.event])


def _checks_for_live_step(
    step: BenchmarkStep,
    *,
    response_hex: str | None,
    response_error: str | None,
    response_valid: bool,
    response_validation_error: str | None,
    response_is_exception: bool | None,
    exception_code: int | None,
    protocol_status: str | None,
    event_log_path: Path | None,
    hmi_observer_configured: bool,
    reply_values: tuple[int, ...] | None,
    process_values: Mapping[str, float],
    process_invariant_results: tuple[ProcessInvariantResult, ...],
) -> dict[str, bool]:
    expects_denial = step.expected_protocol_status.value == "denied"
    checks: dict[str, bool] = {
        "request_hex_present": step.request_hex is not None,
    }
    if not expects_denial:
        checks["response_received"] = response_hex is not None and response_error is None
        checks["response_valid"] = response_valid
        checks["response_matches_request"] = response_validation_error is None
    elif response_hex is not None:
        checks["denied_response_not_malformed"] = (
            response_valid or response_validation_error is None
        )

    if step.expected_response_kind is not None:
        expected_kind = step.expected_response_kind.strip().lower()
        if expected_kind == "normal":
            checks["response_kind"] = (
                response_hex is not None
                and response_valid
                and response_is_exception is False
            )
        elif expected_kind == "exception":
            checks["response_kind"] = (
                response_hex is not None
                and response_valid
                and response_is_exception is True
            )
        elif expected_kind == "none":
            checks["response_kind"] = response_hex is None
        else:
            checks["response_kind"] = False

    if step.expected_exception_code is not None:
        checks["exception_code"] = exception_code == step.expected_exception_code

    if event_log_path is not None:
        checks["protocol_status_observed"] = protocol_status is not None
        checks["protocol_status"] = (
            protocol_status == step.expected_protocol_status.value
        )

    if step.expected_reply_values is not None:
        checks["reply_values"] = reply_values == step.expected_reply_values

    if hmi_observer_configured:
        for variable_id, expected_value in step.expected_process_values.items():
            actual_value = process_values.get(variable_id)
            checks[f"process_value:{variable_id}"] = (
                actual_value is not None
                and abs(float(actual_value) - float(expected_value)) <= 1e-9
            )

    for result in process_invariant_results:
        checks[f"process_invariant:{result.invariant_id}"] = result.passed

    return checks


def _evaluate_live_invariants(
    invariants: Iterable[ProcessInvariant],
    *,
    observer: HMIStateObserver | None,
    before_state: Mapping[str, Any] | None,
    after_state: Mapping[str, Any] | None,
    event: ICSEvent,
    reply_values: tuple[int, ...] | None,
) -> tuple[ProcessInvariantResult, ...]:
    return tuple(
        _evaluate_live_invariant(
            invariant,
            observer=observer,
            before_state=before_state,
            after_state=after_state,
            event=event,
            reply_values=reply_values,
        )
        for invariant in invariants
    )


def _evaluate_live_invariant(
    invariant: ProcessInvariant,
    *,
    observer: HMIStateObserver | None,
    before_state: Mapping[str, Any] | None,
    after_state: Mapping[str, Any] | None,
    event: ICSEvent,
    reply_values: tuple[int, ...] | None,
) -> ProcessInvariantResult:
    if observer is None or after_state is None:
        return _failed(invariant, "missing_hmi_process_observer")
    try:
        if invariant.kind is ProcessInvariantKind.VARIABLE_EQUALS:
            variable_id = _required_text(invariant.variable_id, "variable_id")
            expected = _required_number(invariant.value, "value")
            actual = observer.variable_value(after_state, variable_id)
            passed = actual is not None and _near(actual, expected, invariant.tolerance)
            return ProcessInvariantResult(
                invariant_id=invariant.invariant_id,
                kind=invariant.kind,
                passed=passed,
                reason="variable_equals" if passed else "variable_value_mismatch",
                actual=actual,
                expected=expected,
            )
        if invariant.kind is ProcessInvariantKind.VARIABLE_BETWEEN:
            variable_id = _required_text(invariant.variable_id, "variable_id")
            lower = invariant.minimum
            upper = invariant.maximum
            default_lower, default_upper = observer.bounds_for(variable_id)
            if lower is None:
                lower = default_lower
            if upper is None:
                upper = default_upper
            if lower is None and upper is None:
                raise ValueError("variable_between requires known bounds")
            actual = observer.variable_value(after_state, variable_id)
            passed = actual is not None
            if lower is not None:
                passed = passed and actual >= lower - invariant.tolerance
            if upper is not None:
                passed = passed and actual <= upper + invariant.tolerance
            return ProcessInvariantResult(
                invariant_id=invariant.invariant_id,
                kind=invariant.kind,
                passed=passed,
                reason="variable_between" if passed else "variable_out_of_bounds",
                actual=actual,
                expected={"minimum": lower, "maximum": upper},
            )
        if invariant.kind is ProcessInvariantKind.TREND:
            if before_state is None:
                return _failed(invariant, "missing_before_hmi_state")
            variable_id = _required_text(invariant.variable_id, "variable_id")
            before = observer.variable_value(before_state, variable_id)
            after = observer.variable_value(after_state, variable_id)
            if before is None or after is None:
                return _failed(invariant, "missing_hmi_variable")
            delta = after - before
            direction = _required_direction(invariant)
            if direction is TrendDirection.INCREASE:
                passed = delta > invariant.tolerance
            elif direction is TrendDirection.DECREASE:
                passed = delta < -invariant.tolerance
            else:
                passed = abs(delta) <= invariant.tolerance
            return ProcessInvariantResult(
                invariant_id=invariant.invariant_id,
                kind=invariant.kind,
                passed=passed,
                reason="trend_satisfied" if passed else "trend_violated",
                actual={"before": before, "after": after, "delta": delta},
                expected={"direction": direction.value},
            )
        if invariant.kind is ProcessInvariantKind.RELATION:
            left_variable = _required_text(invariant.left_variable, "left_variable")
            operator = _required_operator(invariant)
            left = observer.variable_value(after_state, left_variable)
            if left is None:
                return _failed(invariant, "missing_left_hmi_variable")
            if invariant.right_variable is not None:
                right = observer.variable_value(after_state, invariant.right_variable)
                if right is None:
                    return _failed(invariant, "missing_right_hmi_variable")
                expected: object = {
                    "operator": operator.value,
                    "right_variable": invariant.right_variable,
                    "right_value": right,
                }
            else:
                right = _required_number(invariant.right_value, "right_value")
                expected = {"operator": operator.value, "right_value": right}
            passed = _compare(left, right, operator, invariant.tolerance)
            return ProcessInvariantResult(
                invariant_id=invariant.invariant_id,
                kind=invariant.kind,
                passed=passed,
                reason="relation_satisfied" if passed else "relation_violated",
                actual=left,
                expected=expected,
            )
        if invariant.kind is ProcessInvariantKind.MOVES_TOWARD:
            if before_state is None:
                return _failed(invariant, "missing_before_hmi_state")
            variable_id = _required_text(invariant.variable_id, "variable_id")
            target_variable = _required_text(
                invariant.target_variable,
                "target_variable",
            )
            before = observer.variable_value(before_state, variable_id)
            after = observer.variable_value(after_state, variable_id)
            target = observer.variable_value(after_state, target_variable)
            if before is None or after is None or target is None:
                return _failed(invariant, "missing_hmi_variable")
            before_distance = abs(before - target)
            after_distance = abs(after - target)
            passed = after_distance <= before_distance + invariant.tolerance
            return ProcessInvariantResult(
                invariant_id=invariant.invariant_id,
                kind=invariant.kind,
                passed=passed,
                reason="moves_toward_target" if passed else "moves_away_from_target",
                actual={
                    "before": before,
                    "after": after,
                    "target": target,
                    "before_distance": before_distance,
                    "after_distance": after_distance,
                },
                expected={"target_variable": target_variable},
            )
        if invariant.kind is ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT:
            if reply_values is None:
                return _failed(invariant, "missing_protocol_reply_values")
            expected_values = observer.register_values_for_event(event, after_state)
            if expected_values is None:
                return _failed(invariant, "event_is_not_mapped_to_hmi_state")
            if invariant.reply_index is not None:
                index = invariant.reply_index
                try:
                    actual: object = reply_values[index]
                    expected: object = expected_values[index]
                except IndexError:
                    return _failed(
                        invariant,
                        "reply_or_expected_value_index_out_of_range",
                        actual=list(reply_values),
                        expected=list(expected_values),
                    )
                passed = int(actual) == int(expected)
            else:
                actual = list(reply_values)
                expected = list(expected_values)
                passed = tuple(reply_values) == tuple(expected_values)
            return ProcessInvariantResult(
                invariant_id=invariant.invariant_id,
                kind=invariant.kind,
                passed=passed,
                reason=(
                    "reply_matches_hmi_process_snapshot"
                    if passed
                    else "reply_hmi_process_snapshot_mismatch"
                ),
                actual=actual,
                expected=expected,
            )
    except (TypeError, ValueError) as exc:
        return _failed(invariant, str(exc))
    return _failed(invariant, f"unsupported_invariant_kind:{invariant.kind}")


def _safe_hmi_snapshot(
    observer: HMIStateObserver | None,
) -> Mapping[str, Any] | None:
    if observer is None:
        return None
    try:
        return observer.snapshot()
    except Exception:
        return None


def _read_process_values(
    observer: HMIStateObserver | None,
    state: Mapping[str, Any] | None,
    variable_ids: Iterable[str],
) -> dict[str, float]:
    if observer is None or state is None:
        return {}
    values: dict[str, float] = {}
    for variable_id in variable_ids:
        value = observer.variable_value(state, str(variable_id))
        if value is not None:
            values[str(variable_id)] = value
    return values


def _latest_event_with_request_hex(
    path: Path,
    normalized_request_hex: str,
) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    found: Mapping[str, Any] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping):
                continue
            metadata = event.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            request_hex = metadata.get("request_hex")
            if request_hex and _clean_hex(str(request_hex)) == normalized_request_hex:
                found = event
    return found


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed before full Modbus response")
        chunks.extend(chunk)
    return bytes(chunks)


def _event_function_code(event: ICSEvent) -> int:
    value = event.metadata.get("function_code")
    if value is not None:
        return int(value)
    return {
        "read_coils": 1,
        "read_discrete_inputs": 2,
        "read_holding_registers": 3,
        "read_input_registers": 4,
        "write_single_coil": 5,
        "write_single_register": 6,
        "write_multiple_coils": 15,
        "write_multiple_registers": 16,
    }.get(event.operation, 0)


def _live_modbus_step_from_request(
    *,
    step_id: str,
    request_hex: str,
    expected_protocol_status: ProtocolTransitionStatus = (
        ProtocolTransitionStatus.ALLOWED
    ),
    expected_response_kind: str = "normal",
    expected_exception_code: int | None = None,
    expected_process_values: Mapping[str, float] | None = None,
    expected_reply_values: tuple[int, ...] | None = None,
    process_invariants: tuple[ProcessInvariant, ...] = (),
    tick_seconds_before: float = 0.0,
    notes: str = "",
) -> BenchmarkStep:
    event = event_from_modbus_tcp_request(
        bytes.fromhex(request_hex),
        context=ModbusHookContext(
            session_id="live-modbus-benchmark",
            source_ip="192.0.2.10",
            actor_id="live-benchmark-actor",
            source_port=55020,
            destination_ip="192.0.2.20",
            destination_port=502,
        ),
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
        tick_seconds_before=tick_seconds_before,
        notes=notes,
    )


def _modbus_read_request(
    *,
    transaction_id: int,
    function_code: int,
    address: int,
    count: int,
    unit_id: int = 1,
) -> str:
    return build_modbus_tcp_read_request(
        transaction_id=transaction_id,
        unit_id=unit_id,
        function_code=function_code,
        address=address,
        count=count,
    )


def _modbus_write_single_coil_request(
    *,
    transaction_id: int,
    address: int,
    energized: bool,
    unit_id: int = 1,
) -> str:
    return build_modbus_tcp_write_single_coil_request(
        transaction_id=transaction_id,
        unit_id=unit_id,
        address=address,
        energized=energized,
    )


def _modbus_write_single_register_request(
    *,
    transaction_id: int,
    address: int,
    value: int,
    unit_id: int = 1,
) -> str:
    return build_modbus_tcp_write_single_register_request(
        transaction_id=transaction_id,
        unit_id=unit_id,
        address=address,
        value=value,
    )


def _modbus_write_multiple_registers_request(
    *,
    transaction_id: int,
    address: int,
    values: Iterable[int],
    unit_id: int = 1,
) -> str:
    return build_modbus_tcp_write_multiple_registers_request(
        transaction_id=transaction_id,
        unit_id=unit_id,
        address=address,
        values=tuple(values),
    )


def _modbus_write_multiple_coils_request(
    *,
    transaction_id: int,
    address: int,
    values: Iterable[int | bool],
    unit_id: int = 1,
) -> str:
    return build_modbus_tcp_write_multiple_coils_request(
        transaction_id=transaction_id,
        unit_id=unit_id,
        address=address,
        values=tuple(values),
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
    raw.extend(int(transaction_id).to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend(length.to_bytes(2, "big"))
    raw.append(int(unit_id))
    raw.extend(pdu)
    return bytes(raw).hex()


def _failed(
    invariant: ProcessInvariant,
    reason: str,
    *,
    actual: object | None = None,
    expected: object | None = None,
) -> ProcessInvariantResult:
    return ProcessInvariantResult(
        invariant_id=invariant.invariant_id,
        kind=invariant.kind,
        passed=False,
        reason=reason,
        actual=actual,
        expected=expected,
    )


def _required_text(value: str | None, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    return str(value)


def _required_number(value: float | None, field_name: str) -> float:
    if value is None:
        raise ValueError(f"{field_name} is required")
    return float(value)


def _required_operator(invariant: ProcessInvariant) -> InvariantComparison:
    if invariant.operator is None:
        raise ValueError("operator is required")
    return invariant.operator


def _required_direction(invariant: ProcessInvariant) -> TrendDirection:
    if invariant.direction is None:
        raise ValueError("direction is required")
    return invariant.direction


def _near(left: float, right: float, tolerance: float) -> bool:
    return abs(float(left) - float(right)) <= float(tolerance)


def _compare(
    left: float,
    right: float,
    operator: InvariantComparison,
    tolerance: float,
) -> bool:
    if operator is InvariantComparison.LT:
        return left < right - tolerance
    if operator is InvariantComparison.LE:
        return left <= right + tolerance
    if operator is InvariantComparison.EQ:
        return _near(left, right, tolerance)
    if operator is InvariantComparison.GE:
        return left >= right - tolerance
    if operator is InvariantComparison.GT:
        return left > right + tolerance
    return False


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _clean_hex(payload_hex: str) -> str:
    return "".join(str(payload_hex).split()).lower()
