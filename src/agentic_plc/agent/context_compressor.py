from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agentic_plc.agent.process_context import ExposedProcessPoint, PhysicalProcessContext
from agentic_plc.agent.process_memory import (
    ActorProcessMemorySummary,
    mapped_points_for_event,
    register_area_for_event,
    summarize_actor_process_memory,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.world.registers import RegisterArea


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Bound the prompt-facing process context."""

    max_points: int = 12
    max_events: int = 5
    max_touched_points: int = 8


@dataclass(frozen=True, slots=True)
class CompressedProcessPoint:
    """One selected PLC-facing process point with auditable selection reasons."""

    variable_id: str
    role: str
    name: str
    value: float
    unit: str | None
    minimum: float | None
    maximum: float | None
    protocol: str | None
    table: str | None
    address: int | None
    access: str
    scale: float
    tag: str | None
    writable: bool
    encoded_value: int | None = None
    score: float = 0.0
    reason_codes: list[str] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "variable_id": self.variable_id,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "protocol": self.protocol,
            "table": self.table,
            "address": self.address,
            "access": self.access,
            "scale": self.scale,
            "tag": self.tag,
            "writable": self.writable,
            "encoded_value": self.encoded_value,
            "reason_codes": self.reason_codes,
        }


@dataclass(frozen=True, slots=True)
class CompressedProcessContext:
    """Prompt payload for a generic physical-process-aware agent."""

    process_card: dict[str, Any]
    request_focus: dict[str, Any] | None
    exposed_points: list[CompressedProcessPoint]
    writable_variable_ids: list[str]
    actor_memory: ActorProcessMemorySummary | None
    compression: dict[str, Any]

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "process_card": self.process_card,
            "request_focus": self.request_focus,
            "exposed_points": [
                point.to_prompt_dict()
                for point in self.exposed_points
            ],
            "writable_variable_ids": self.writable_variable_ids,
            "actor_memory": (
                self.actor_memory.to_prompt_dict()
                if self.actor_memory is not None
                else None
            ),
            "compression": self.compression,
        }

    def to_prompt_json(self) -> str:
        return json.dumps(self.to_prompt_dict(), sort_keys=True)


class ProcessContextCompressor:
    """Deterministic process-aware context compression.

    The compressor keeps exact protocol-critical fields and ranks process
    variables by their relevance to the latest request, recent actor touches,
    controllability, and physical constraint proximity. It deliberately avoids
    lossy natural-language summarization for numeric PLC points.
    """

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self._budget = budget or ContextBudget()

    def compress(
        self,
        context: PhysicalProcessContext,
        events: list[ICSEvent],
    ) -> CompressedProcessContext:
        snapshot = context.snapshot()
        all_points = context.exposed_points()
        latest = events[-1] if events else None
        actor_memory = summarize_actor_process_memory(
            events,
            context,
            max_events=self._budget.max_events,
            max_touched_points=self._budget.max_touched_points,
        )
        selected, ranking = self._select_points(context, all_points, events)
        return CompressedProcessContext(
            process_card={
                "process_id": context.process_id,
                "backend_name": context.backend_name,
                "scenario_id": context.scenario.scenario_id if context.scenario else None,
                "plc_area": context.scenario.plc_area if context.scenario else None,
                "protocol": context.protocol,
                "revision": snapshot.revision,
                "simulated_seconds": snapshot.simulated_seconds,
                "measurement_count": len(snapshot.measurements),
                "manipulated_variable_count": len(snapshot.manipulated_variables),
                "setpoint_count": len(snapshot.setpoints),
                "metadata": {
                    key: value
                    for key, value in snapshot.metadata.items()
                    if key not in {"overrides"}
                },
            },
            request_focus=self._request_focus(context, latest),
            exposed_points=selected,
            writable_variable_ids=context.writable_variable_ids(),
            actor_memory=actor_memory,
            compression={
                "strategy": "process_aware_context_compression_v1",
                "max_points": self._budget.max_points,
                "max_events": self._budget.max_events,
                "input_point_count": len(all_points),
                "selected_point_count": len(selected),
                "ranking": ranking,
            },
        )

    def _select_points(
        self,
        context: PhysicalProcessContext,
        points: list[ExposedProcessPoint],
        events: list[ICSEvent],
    ) -> tuple[list[CompressedProcessPoint], list[dict[str, Any]]]:
        latest = events[-1] if events else None
        latest_matches = (
            {
                _point_key(point)
                for point in mapped_points_for_event(context, latest)
            }
            if latest is not None
            else set()
        )
        touch_counts = Counter(
            _point_key(point)
            for event in events[-20:]
            for point in mapped_points_for_event(context, event)
        )
        scored = [
            self._score_point(point, events, latest_matches, touch_counts)
            for point in points
        ]
        scored.sort(
            key=lambda item: (
                -item[1],
                item[0].table or "",
                item[0].address if item[0].address is not None else 10**9,
                item[0].variable_id,
            )
        )
        selected = [
            self._compress_point(context, point, score, reason_codes)
            for point, score, reason_codes in scored[: self._budget.max_points]
        ]
        ranking = [
            {
                "variable_id": point.variable_id,
                "score": score,
                "reason_codes": reason_codes,
            }
            for point, score, reason_codes in scored
        ]
        return selected, ranking

    def _score_point(
        self,
        point: ExposedProcessPoint,
        events: list[ICSEvent],
        latest_matches: set[tuple[str, str | None, int | None]],
        touch_counts: Counter[tuple[str, str | None, int | None]],
    ) -> tuple[ExposedProcessPoint, float, list[str]]:
        score = 0.0
        reason_codes: list[str] = []

        latest = events[-1] if events else None
        key = _point_key(point)
        if key in latest_matches:
            score += 1000.0
            reason_codes.append("current_request_address")

        touch_count = touch_counts[key]
        if touch_count:
            score += 120.0 + min(80.0, touch_count * 10.0)
            reason_codes.append("recent_actor_interest")

        if point.writable:
            score += 85.0
            reason_codes.append("writable_control_surface")
            if latest and latest.intent in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}:
                score += 200.0
                reason_codes.append("write_relevant")

        if latest and latest.intent is Intent.READ_PROCESS and point.role == "measurement":
            score += 40.0
            reason_codes.append("read_relevant_measurement")
        elif latest and latest.intent in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}:
            if point.role in {"setpoint", "manipulated_variable"}:
                score += 40.0
                reason_codes.append("control_relevant")

        constraint_code = _constraint_reason(point)
        if constraint_code is not None:
            score += 65.0
            reason_codes.append(constraint_code)

        if not reason_codes:
            reason_codes.append("background_process_state")
        return point, score, reason_codes

    def _compress_point(
        self,
        context: PhysicalProcessContext,
        point: ExposedProcessPoint,
        score: float,
        reason_codes: list[str],
    ) -> CompressedProcessPoint:
        return CompressedProcessPoint(
            variable_id=point.variable_id,
            role=point.role,
            name=point.name,
            value=point.value,
            unit=point.unit,
            minimum=point.minimum,
            maximum=point.maximum,
            protocol=point.protocol,
            table=point.table,
            address=point.address,
            access=point.access,
            scale=point.scale,
            tag=point.tag,
            writable=point.writable,
            encoded_value=_encoded_register_value(context, point),
            score=score,
            reason_codes=reason_codes,
        )

    def _request_focus(
        self,
        context: PhysicalProcessContext,
        event: ICSEvent | None,
    ) -> dict[str, Any] | None:
        if event is None:
            return None
        area = register_area_for_event(event)
        register_values = None
        if area in {RegisterArea.INPUT_REGISTERS, RegisterArea.HOLDING_REGISTERS}:
            try:
                register_values = context.values_for_modbus_event(event)
            except ValueError:
                register_values = None
        return {
            "protocol": event.protocol,
            "intent": str(event.intent.value),
            "operation": event.operation,
            "transaction_id": event.transaction_id,
            "unit_id": event.unit_id,
            "function_code": event.metadata.get("function_code"),
            "table": area.value if area is not None else None,
            "address": event.address,
            "count": event.count,
            "requested_value": event.requested_value,
            "register_values": register_values,
        }


def _encoded_register_value(
    context: PhysicalProcessContext,
    point: ExposedProcessPoint,
) -> int | None:
    if context.register_map is None or point.table is None or point.address is None:
        return None
    try:
        area = RegisterArea(point.table)
        return context.register_map.read(area, point.address, 1)[0]
    except ValueError:
        return None


def _constraint_reason(point: ExposedProcessPoint) -> str | None:
    if point.minimum is None or point.maximum is None:
        return None
    span = point.maximum - point.minimum
    if span <= 0:
        return None
    normalized = (point.value - point.minimum) / span
    if normalized <= 0.05:
        return "near_lower_bound"
    if normalized >= 0.95:
        return "near_upper_bound"
    return None


def _point_key(point: ExposedProcessPoint) -> tuple[str, str | None, int | None]:
    return (point.variable_id, point.table, point.address)
