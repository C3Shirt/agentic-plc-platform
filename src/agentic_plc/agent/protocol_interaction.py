from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from agentic_plc.agent.process_memory import register_area_for_event
from agentic_plc.contracts.events import ICSEvent, Intent


class InteractionPhase(StrEnum):
    """Protocol-level attacker interaction phase."""

    INITIAL_CONTACT = "initial_contact"
    DISCOVERY = "discovery"
    ADDRESS_PROBING = "address_probing"
    REGISTER_MAPPING = "register_mapping"
    PROCESS_MONITORING = "process_monitoring"
    WRITE_ATTEMPT = "write_attempt"
    EFFECT_VERIFICATION = "effect_verification"
    FAULT_PROBING = "fault_probing"


@dataclass(frozen=True, slots=True)
class ProtocolAddressRange:
    protocol: str
    table: str | None
    address: int
    count: int = 1

    @property
    def end_exclusive(self) -> int:
        return self.address + self.count

    def overlaps(self, other: ProtocolAddressRange) -> bool:
        return (
            self.protocol == other.protocol
            and self.table == other.table
            and self.address < other.end_exclusive
            and other.address < self.end_exclusive
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "table": self.table,
            "address": self.address,
            "count": self.count,
            "end_exclusive": self.end_exclusive,
        }


@dataclass(slots=True)
class ActorProtocolState:
    """Per-actor state derived from normalized industrial protocol events."""

    actor_id: str
    protocol: str
    phase: InteractionPhase = InteractionPhase.INITIAL_CONTACT
    phase_reason: str = "first_protocol_observation"
    event_count: int = 0
    sessions: set[str] = field(default_factory=set)
    intent_counts: Counter[str] = field(default_factory=Counter)
    operation_counts: Counter[str] = field(default_factory=Counter)
    touched_ranges: list[ProtocolAddressRange] = field(default_factory=list)
    read_ranges: list[ProtocolAddressRange] = field(default_factory=list)
    write_ranges: list[ProtocolAddressRange] = field(default_factory=list)
    invalid_or_unsupported_count: int = 0
    consecutive_error_count: int = 0
    events_since_write: int | None = None
    last_write_range: ProtocolAddressRange | None = None
    last_event_id: str | None = None
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    @property
    def unique_touched_address_count(self) -> int:
        return len(
            {
                (item.protocol, item.table, item.address)
                for item in self.touched_ranges
            }
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "protocol": self.protocol,
            "phase": self.phase.value,
            "phase_reason": self.phase_reason,
            "event_count": self.event_count,
            "sessions": sorted(self.sessions),
            "intent_counts": dict(sorted(self.intent_counts.items())),
            "operation_counts": dict(sorted(self.operation_counts.items())),
            "unique_touched_address_count": self.unique_touched_address_count,
            "invalid_or_unsupported_count": self.invalid_or_unsupported_count,
            "consecutive_error_count": self.consecutive_error_count,
            "events_since_write": self.events_since_write,
            "last_write_range": (
                self.last_write_range.to_prompt_dict()
                if self.last_write_range is not None
                else None
            ),
            "recent_touched_ranges": [
                item.to_prompt_dict()
                for item in self.touched_ranges[-8:]
            ],
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


class ProtocolIntentTracker:
    """State tracker for protocol-facing multi-turn honeypot interaction."""

    def __init__(self, *, max_ranges_per_actor: int = 64) -> None:
        self._max_ranges_per_actor = max_ranges_per_actor
        self._states: dict[tuple[str, str], ActorProtocolState] = {}

    def observe(self, event: ICSEvent) -> tuple[ActorProtocolState, ICSEvent]:
        actor_id = event.actor_id or f"ip:{event.source_ip}"
        protocol = event.protocol.lower()
        key = (actor_id, protocol)
        state = self._states.get(key)
        if state is None:
            state = ActorProtocolState(actor_id=actor_id, protocol=protocol)

        previous_write = state.last_write_range
        previous_events_since_write = state.events_since_write
        touched_range = address_range_for_event(event)

        state.event_count += 1
        state.sessions.add(event.session_id)
        state.intent_counts[event.intent.value] += 1
        state.operation_counts[event.operation] += 1
        state.last_event_id = event.event_id
        state.first_timestamp = state.first_timestamp or event.timestamp
        state.last_timestamp = event.timestamp

        if event.intent in {Intent.INVALID_ADDRESS, Intent.UNSUPPORTED_OPERATION}:
            state.invalid_or_unsupported_count += 1
            state.consecutive_error_count += 1
        else:
            state.consecutive_error_count = 0

        if touched_range is not None:
            state.touched_ranges.append(touched_range)
            if event.intent is Intent.READ_PROCESS:
                state.read_ranges.append(touched_range)
            elif event.intent in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}:
                state.write_ranges.append(touched_range)
                state.last_write_range = touched_range
                state.events_since_write = 0
            self._trim_ranges(state)

        if (
            event.intent not in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}
            and state.events_since_write is not None
        ):
            state.events_since_write += 1

        state.phase, state.phase_reason = classify_interaction_phase(
            event,
            state,
            previous_write=previous_write,
            previous_events_since_write=previous_events_since_write,
        )
        self._states[key] = state
        return state, annotate_event_with_protocol_state(event, state)

    def observe_many(self, events: list[ICSEvent]) -> list[ICSEvent]:
        annotated: list[ICSEvent] = []
        for event in events:
            _, enriched = self.observe(event)
            annotated.append(enriched)
        return annotated

    def state_for(self, actor_id: str, protocol: str) -> ActorProtocolState | None:
        return self._states.get((actor_id, protocol.lower()))

    def states(self) -> list[ActorProtocolState]:
        return list(self._states.values())

    def reset(self) -> None:
        self._states.clear()

    def _trim_ranges(self, state: ActorProtocolState) -> None:
        limit = self._max_ranges_per_actor
        if len(state.touched_ranges) > limit:
            state.touched_ranges = state.touched_ranges[-limit:]
        if len(state.read_ranges) > limit:
            state.read_ranges = state.read_ranges[-limit:]
        if len(state.write_ranges) > limit:
            state.write_ranges = state.write_ranges[-limit:]


def classify_interaction_phase(
    event: ICSEvent,
    state: ActorProtocolState,
    *,
    previous_write: ProtocolAddressRange | None = None,
    previous_events_since_write: int | None = None,
) -> tuple[InteractionPhase, str]:
    if event.intent in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}:
        return InteractionPhase.WRITE_ATTEMPT, "latest_event_is_write"

    touched_range = address_range_for_event(event)
    if event.intent is Intent.READ_PROCESS and previous_write is not None:
        recent_after_write = (
            previous_events_since_write is None
            or previous_events_since_write <= 5
        )
        if touched_range is not None and touched_range.overlaps(previous_write):
            return InteractionPhase.EFFECT_VERIFICATION, "read_back_written_range"
        if recent_after_write:
            return InteractionPhase.EFFECT_VERIFICATION, "read_after_recent_write"

    if state.consecutive_error_count >= 2:
        return InteractionPhase.ADDRESS_PROBING, "consecutive_invalid_or_unsupported"
    if state.invalid_or_unsupported_count >= 3:
        return InteractionPhase.ADDRESS_PROBING, "repeated_invalid_or_unsupported"

    if event.intent in {Intent.DISCOVER, Intent.READ_IDENTITY, Intent.AUTH_ATTEMPT}:
        return InteractionPhase.DISCOVERY, "identity_or_discovery_event"

    if event.intent is Intent.FILE_TRANSFER:
        return InteractionPhase.FAULT_PROBING, "artifact_or_file_interest"

    if event.intent is Intent.READ_PROCESS:
        if state.unique_touched_address_count >= 5:
            return InteractionPhase.REGISTER_MAPPING, "many_unique_protocol_addresses"
        if state.intent_counts[Intent.READ_PROCESS.value] >= 2:
            return InteractionPhase.PROCESS_MONITORING, "repeated_process_reads"
        return InteractionPhase.PROCESS_MONITORING, "process_read"

    return InteractionPhase.INITIAL_CONTACT, "insufficient_protocol_history"


def annotate_event_with_protocol_state(
    event: ICSEvent,
    state: ActorProtocolState,
) -> ICSEvent:
    metadata = {
        **event.metadata,
        "interaction_phase": state.phase.value,
        "interaction_phase_reason": state.phase_reason,
        "actor_protocol_event_count": state.event_count,
        "actor_protocol_intent_counts": dict(sorted(state.intent_counts.items())),
        "actor_protocol_unique_touched_addresses": state.unique_touched_address_count,
    }
    if state.last_write_range is not None:
        metadata["actor_protocol_last_write_range"] = (
            state.last_write_range.to_prompt_dict()
        )
    return replace(event, metadata=metadata)


def address_range_for_event(event: ICSEvent) -> ProtocolAddressRange | None:
    if event.address is None:
        return None
    area = register_area_for_event(event)
    return ProtocolAddressRange(
        protocol=event.protocol.lower(),
        table=area.value if area is not None else None,
        address=int(event.address),
        count=max(1, int(event.count or 1)),
    )
