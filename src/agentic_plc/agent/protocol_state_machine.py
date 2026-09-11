from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol

from agentic_plc.contracts.events import ICSEvent, Intent


class ProtocolTransitionStatus(StrEnum):
    """Outcome of checking a protocol event against a session state machine."""

    ALLOWED = "allowed"
    ANOMALOUS = "anomalous"
    DENIED = "denied"
    NO_PROFILE = "no_profile"


class ProtocolSessionPhase(StrEnum):
    """Generic session phases shared by concrete protocol profiles."""

    NEW = "new"
    READY = "ready"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ProtocolTransitionDecision:
    protocol: str
    session_id: str
    actor_id: str
    from_state: str
    to_state: str
    status: ProtocolTransitionStatus
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status in {
            ProtocolTransitionStatus.ALLOWED,
            ProtocolTransitionStatus.ANOMALOUS,
            ProtocolTransitionStatus.NO_PROFILE,
        }

    def to_metadata(self) -> dict[str, Any]:
        return {
            "protocol_fsm_profile": self.protocol,
            "protocol_fsm_allowed": self.allowed,
            "protocol_fsm_status": self.status.value,
            "protocol_fsm_from_state": self.from_state,
            "protocol_fsm_to_state": self.to_state,
            "protocol_fsm_reason": self.reason,
            "protocol_fsm_details": self.details,
        }


@dataclass(slots=True)
class ProtocolSessionState:
    actor_id: str
    protocol: str
    session_id: str
    phase: ProtocolSessionPhase = ProtocolSessionPhase.NEW
    event_count: int = 0
    allowed_count: int = 0
    anomaly_count: int = 0
    denied_count: int = 0
    unit_ids: set[int] = field(default_factory=set)
    operation_counts: Counter[str] = field(default_factory=Counter)
    function_counts: Counter[int] = field(default_factory=Counter)
    transaction_request_signatures: dict[int, tuple[Any, ...]] = field(
        default_factory=dict
    )
    last_transaction_id: int | None = None
    last_event_id: str | None = None
    last_reason: str | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "protocol": self.protocol,
            "session_id": self.session_id,
            "phase": self.phase.value,
            "event_count": self.event_count,
            "allowed_count": self.allowed_count,
            "anomaly_count": self.anomaly_count,
            "denied_count": self.denied_count,
            "unit_ids": sorted(self.unit_ids),
            "operation_counts": dict(sorted(self.operation_counts.items())),
            "function_counts": {
                str(key): value for key, value in sorted(self.function_counts.items())
            },
            "last_transaction_id": self.last_transaction_id,
            "last_event_id": self.last_event_id,
            "last_reason": self.last_reason,
        }


class ProtocolStateMachine(Protocol):
    """Concrete protocol FSM interface used by adapters and runtime."""

    protocol: str

    def observe(self, event: ICSEvent) -> tuple[ProtocolTransitionDecision, ICSEvent]:
        raise NotImplementedError

    def state_for(
        self,
        actor_id: str,
        session_id: str,
    ) -> ProtocolSessionState | None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


class ProtocolStateMachineRegistry:
    """Dispatch events to protocol-specific state machines.

    Unknown protocols are deliberately allowed and annotated as `no_profile`.
    This keeps deterministic adapters working while making unsupported protocol
    coverage visible to tests and prompts.
    """

    def __init__(
        self,
        profiles: list[ProtocolStateMachine] | None = None,
    ) -> None:
        profiles = profiles or [ModbusTcpStateMachine()]
        self._profiles = {
            _normalize_protocol(profile.protocol): profile for profile in profiles
        }

    def observe(self, event: ICSEvent) -> tuple[ProtocolTransitionDecision, ICSEvent]:
        protocol = _normalize_protocol(event.protocol)
        profile = self._profiles.get(protocol)
        if profile is None:
            decision = ProtocolTransitionDecision(
                protocol=protocol,
                session_id=event.session_id,
                actor_id=_actor_id(event),
                from_state=ProtocolSessionPhase.READY.value,
                to_state=ProtocolSessionPhase.READY.value,
                status=ProtocolTransitionStatus.NO_PROFILE,
                reason="no_protocol_state_machine_profile",
            )
            return decision, annotate_event_with_protocol_fsm(event, decision)
        return profile.observe(event)

    def observe_many(self, events: list[ICSEvent]) -> list[ICSEvent]:
        annotated: list[ICSEvent] = []
        for event in events:
            _, enriched = self.observe(event)
            annotated.append(enriched)
        return annotated

    def state_for(
        self,
        actor_id: str,
        protocol: str,
        session_id: str,
    ) -> ProtocolSessionState | None:
        profile = self._profiles.get(_normalize_protocol(protocol))
        if profile is None:
            return None
        return profile.state_for(actor_id, session_id)

    def reset(self) -> None:
        for profile in self._profiles.values():
            profile.reset()


class ModbusTcpStateMachine:
    """Session-level state machine for Modbus TCP request events.

    Modbus TCP is session-light: it does not require an application-layer
    handshake or authentication before reads/writes. Therefore this profile
    treats well-formed requests as legal, but still tracks transaction ids,
    function-code/operation consistency, unit ids, and request shape. Suspicious
    transaction reuse is annotated as anomalous rather than denied because
    Modbus transaction ids may legally be reused after a response.
    """

    protocol = "modbus"
    _supported_functions = frozenset({1, 2, 3, 4, 5, 6, 15, 16})
    _operation_for_function = {
        1: "read_coils",
        2: "read_discrete_inputs",
        3: "read_holding_registers",
        4: "read_input_registers",
        5: "write_single_coil",
        6: "write_single_register",
        15: "write_multiple_coils",
        16: "write_multiple_registers",
    }

    def __init__(self, *, max_transaction_signatures: int = 128) -> None:
        self._states: dict[tuple[str, str], ProtocolSessionState] = {}
        self._max_transaction_signatures = max_transaction_signatures

    def observe(self, event: ICSEvent) -> tuple[ProtocolTransitionDecision, ICSEvent]:
        actor_id = _actor_id(event)
        key = (actor_id, event.session_id)
        state = self._states.get(key)
        if state is None:
            state = ProtocolSessionState(
                actor_id=actor_id,
                protocol=self.protocol,
                session_id=event.session_id,
            )

        from_state = state.phase.value
        status, reason, details = self._evaluate(event, state)
        if status is not ProtocolTransitionStatus.DENIED:
            state.phase = ProtocolSessionPhase.READY
        decision = ProtocolTransitionDecision(
            protocol=self.protocol,
            session_id=event.session_id,
            actor_id=actor_id,
            from_state=from_state,
            to_state=state.phase.value,
            status=status,
            reason=reason,
            details=details,
        )
        self._record(state, event, decision)
        self._states[key] = state
        return decision, annotate_event_with_protocol_fsm(event, decision, state)

    def state_for(
        self,
        actor_id: str,
        session_id: str,
    ) -> ProtocolSessionState | None:
        return self._states.get((actor_id, session_id))

    def reset(self) -> None:
        self._states.clear()

    def _evaluate(
        self,
        event: ICSEvent,
        state: ProtocolSessionState,
    ) -> tuple[ProtocolTransitionStatus, str, dict[str, Any]]:
        function_code = _modbus_function_code(event)
        transaction_id = _parse_transaction_id(event.transaction_id)
        details: dict[str, Any] = {
            "operation": event.operation,
            "intent": event.intent.value,
            "function_code": function_code,
            "transaction_id": transaction_id,
            "unit_id": event.unit_id,
            "address": event.address,
            "count": event.count,
        }

        if transaction_id is None:
            return (
                ProtocolTransitionStatus.DENIED,
                "missing_or_invalid_modbus_transaction_id",
                details,
            )
        if event.unit_id is None or not 0 <= int(event.unit_id) <= 247:
            return ProtocolTransitionStatus.DENIED, "invalid_modbus_unit_id", details
        if function_code is None:
            return (
                ProtocolTransitionStatus.DENIED,
                "missing_modbus_function_code",
                details,
            )
        if function_code not in self._supported_functions:
            return (
                ProtocolTransitionStatus.DENIED,
                "unsupported_modbus_function",
                details,
            )

        expected_operation = self._operation_for_function[function_code]
        if event.operation != expected_operation:
            details["expected_operation"] = expected_operation
            return (
                ProtocolTransitionStatus.DENIED,
                "modbus_function_operation_mismatch",
                details,
            )

        reason = self._validate_request_shape(event, function_code)
        if reason is not None:
            return ProtocolTransitionStatus.DENIED, reason, details

        signature = _request_signature(event, function_code)
        prior_signature = state.transaction_request_signatures.get(transaction_id)
        if prior_signature is not None and prior_signature != signature:
            details["previous_signature"] = list(prior_signature)
            details["current_signature"] = list(signature)
            return (
                ProtocolTransitionStatus.ANOMALOUS,
                "duplicate_transaction_id_reused_with_different_request",
                details,
            )

        return ProtocolTransitionStatus.ALLOWED, "modbus_request_allowed", details

    def _validate_request_shape(
        self,
        event: ICSEvent,
        function_code: int,
    ) -> str | None:
        if event.address is None:
            return "missing_modbus_address"
        if not 0 <= int(event.address) <= 65535:
            return "invalid_modbus_address"
        if event.count is None or int(event.count) <= 0:
            return "missing_or_invalid_modbus_count"

        count = int(event.count)
        if function_code in {1, 2} and count > 2000:
            return "modbus_bit_read_count_too_large"
        if function_code in {3, 4} and count > 125:
            return "modbus_register_read_count_too_large"
        if function_code == 15 and count > 1968:
            return "modbus_multi_coil_write_count_too_large"
        if function_code == 16 and count > 123:
            return "modbus_multi_register_write_count_too_large"

        if function_code in {1, 2, 3, 4}:
            return None

        values = _requested_values(event)
        if not values:
            return "missing_modbus_write_value"
        if function_code in {5, 6} and len(values) != 1:
            return "modbus_single_write_value_count_mismatch"
        if function_code in {15, 16} and len(values) != count:
            return "modbus_multiple_write_value_count_mismatch"
        if function_code in {5, 15} and any(value not in {0, 1, 0x0000, 0xFF00} for value in values):
            return "invalid_modbus_coil_write_value"
        if function_code in {6, 16} and any(not 0 <= value <= 65535 for value in values):
            return "invalid_modbus_register_write_value"
        return None

    def _record(
        self,
        state: ProtocolSessionState,
        event: ICSEvent,
        decision: ProtocolTransitionDecision,
    ) -> None:
        state.event_count += 1
        if decision.status is ProtocolTransitionStatus.DENIED:
            state.denied_count += 1
        elif decision.status is ProtocolTransitionStatus.ANOMALOUS:
            state.anomaly_count += 1
            state.allowed_count += 1
        else:
            state.allowed_count += 1

        function_code = _modbus_function_code(event)
        transaction_id = _parse_transaction_id(event.transaction_id)
        state.operation_counts[event.operation] += 1
        if function_code is not None:
            state.function_counts[function_code] += 1
        if event.unit_id is not None:
            state.unit_ids.add(int(event.unit_id))
        if transaction_id is not None:
            state.last_transaction_id = transaction_id
            state.transaction_request_signatures[transaction_id] = _request_signature(
                event,
                function_code,
            )
            self._trim_transaction_signatures(state)
        state.last_event_id = event.event_id
        state.last_reason = decision.reason

    def _trim_transaction_signatures(self, state: ProtocolSessionState) -> None:
        extra = len(state.transaction_request_signatures) - self._max_transaction_signatures
        if extra <= 0:
            return
        for transaction_id in list(state.transaction_request_signatures)[:extra]:
            del state.transaction_request_signatures[transaction_id]


def annotate_event_with_protocol_fsm(
    event: ICSEvent,
    decision: ProtocolTransitionDecision,
    state: ProtocolSessionState | None = None,
) -> ICSEvent:
    metadata = {**event.metadata, **decision.to_metadata()}
    if state is not None:
        metadata.update(
            {
                "protocol_fsm_session_event_count": state.event_count,
                "protocol_fsm_allowed_count": state.allowed_count,
                "protocol_fsm_anomaly_count": state.anomaly_count,
                "protocol_fsm_denied_count": state.denied_count,
            }
        )
    return replace(event, metadata=metadata)


def _normalize_protocol(protocol: str) -> str:
    normalized = protocol.lower().replace("-", "_")
    if normalized in {"modbus_tcp", "modbus/tcp"}:
        return "modbus"
    return normalized


def _actor_id(event: ICSEvent) -> str:
    return event.actor_id or f"ip:{event.source_ip}"


def _modbus_function_code(event: ICSEvent) -> int | None:
    function_code = event.metadata.get("function_code")
    if function_code is not None:
        try:
            return int(function_code)
        except (TypeError, ValueError):
            return None
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


def _parse_transaction_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value), 0)
    except (TypeError, ValueError):
        return None
    if not 0 <= parsed <= 65535:
        return None
    return parsed


def _requested_values(event: ICSEvent) -> tuple[int, ...]:
    if event.requested_value is None:
        return ()
    value = event.requested_value
    if isinstance(value, bool):
        return (int(value),)
    if isinstance(value, int):
        return (value,)
    if isinstance(value, float) and value.is_integer():
        return (int(value),)
    if isinstance(value, (list, tuple)):
        values: list[int] = []
        for item in value:
            if isinstance(item, bool):
                values.append(int(item))
            elif isinstance(item, int):
                values.append(item)
            elif isinstance(item, float) and item.is_integer():
                values.append(int(item))
            else:
                return ()
        return tuple(values)
    return ()


def _request_signature(
    event: ICSEvent,
    function_code: int | None,
) -> tuple[Any, ...]:
    return (
        function_code,
        event.unit_id,
        event.operation,
        event.address,
        event.count,
        _requested_values(event),
    )
