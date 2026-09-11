from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agentic_plc.agent.process_context import ExposedProcessPoint, PhysicalProcessContext
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.telemetry.serialization import event_to_dict
from agentic_plc.world.registers import RegisterArea


@dataclass(frozen=True, slots=True)
class TouchedProcessPoint:
    """A protocol-facing point the current actor has touched recently."""

    variable_id: str
    tag: str | None
    table: str | None
    address: int | None
    operation: str
    intent: str
    count: int = 1

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "variable_id": self.variable_id,
            "tag": self.tag,
            "table": self.table,
            "address": self.address,
            "operation": self.operation,
            "intent": self.intent,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class ActorProcessMemorySummary:
    """Compact, deterministic episodic memory for one attacker/connection."""

    actor_id: str
    event_count: int
    intent_counts: dict[str, int] = field(default_factory=dict)
    touched_points: list[TouchedProcessPoint] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "event_count": self.event_count,
            "intent_counts": self.intent_counts,
            "touched_points": [
                touched_point.to_prompt_dict()
                for touched_point in self.touched_points
            ],
            "recent_events": self.recent_events,
        }


def summarize_actor_process_memory(
    events: list[ICSEvent],
    context: PhysicalProcessContext | None = None,
    *,
    max_events: int = 5,
    max_touched_points: int = 8,
) -> ActorProcessMemorySummary | None:
    """Summarize recent actor behavior without free-form LLM summarization."""

    if not events:
        return None

    latest = events[-1]
    actor_id = latest.actor_id or f"ip:{latest.source_ip}"
    relevant_events = [
        event for event in events if _same_actor(event, latest)
    ]
    intent_counts = Counter(str(event.intent.value) for event in relevant_events)

    touched_points: list[TouchedProcessPoint] = []
    seen: set[tuple[str, str | None, int | None]] = set()
    if context is not None:
        for event in reversed(relevant_events):
            for point in mapped_points_for_event(context, event):
                key = (point.variable_id, point.table, point.address)
                if key in seen:
                    continue
                seen.add(key)
                touched_points.append(
                    TouchedProcessPoint(
                        variable_id=point.variable_id,
                        tag=point.tag,
                        table=point.table,
                        address=point.address,
                        operation=event.operation,
                        intent=str(event.intent.value),
                        count=int(event.count or 1),
                    )
                )
                if len(touched_points) >= max_touched_points:
                    break
            if len(touched_points) >= max_touched_points:
                break

    return ActorProcessMemorySummary(
        actor_id=actor_id,
        event_count=len(relevant_events),
        intent_counts=dict(sorted(intent_counts.items())),
        touched_points=touched_points,
        recent_events=[
            compact_event_for_memory(event)
            for event in relevant_events[-max_events:]
        ],
    )


def compact_event_for_memory(event: ICSEvent) -> dict[str, Any]:
    """Retain event fields that affect process-aware protocol generation."""

    data = event_to_dict(event)
    keep = {
        "protocol",
        "session_id",
        "source_ip",
        "actor_id",
        "intent",
        "operation",
        "transaction_id",
        "unit_id",
        "address",
        "count",
        "previous_value",
        "requested_value",
        "resulting_value",
        "result",
        "world_revision",
    }
    compact = {key: data[key] for key in keep if key in data and data[key] is not None}
    metadata = {
        key: event.metadata[key]
        for key in ("function_code", "exception_code")
        if key in event.metadata
    }
    if metadata:
        compact["metadata"] = metadata
    return compact


def mapped_points_for_event(
    context: PhysicalProcessContext,
    event: ICSEvent,
) -> list[ExposedProcessPoint]:
    """Return exposed points addressed by an event, if the event maps to a table."""

    area = register_area_for_event(event)
    if area is None or event.address is None:
        return []
    count = max(1, int(event.count or 1))
    addresses = set(range(int(event.address), int(event.address) + count))
    return [
        point
        for point in context.exposed_points()
        if point.table == area.value and point.address in addresses
    ]


def register_area_for_event(event: ICSEvent) -> RegisterArea | None:
    function_code = event.metadata.get("function_code")
    if function_code is None:
        operation = event.operation.lower()
        if "holding" in operation:
            return RegisterArea.HOLDING_REGISTERS
        if "input" in operation:
            return RegisterArea.INPUT_REGISTERS
        if "coil" in operation:
            return RegisterArea.COILS
        if "discrete" in operation:
            return RegisterArea.DISCRETE_INPUTS
        return None

    function_code = int(function_code)
    return {
        1: RegisterArea.COILS,
        2: RegisterArea.DISCRETE_INPUTS,
        3: RegisterArea.HOLDING_REGISTERS,
        4: RegisterArea.INPUT_REGISTERS,
        5: RegisterArea.COILS,
        6: RegisterArea.HOLDING_REGISTERS,
        15: RegisterArea.COILS,
        16: RegisterArea.HOLDING_REGISTERS,
    }.get(function_code)


def _same_actor(event: ICSEvent, latest: ICSEvent) -> bool:
    if latest.actor_id is not None:
        return event.actor_id == latest.actor_id
    return event.source_ip == latest.source_ip
