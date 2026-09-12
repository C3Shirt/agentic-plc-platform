from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping

from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.processes.base import ProcessSnapshot


class ProcessInvariantKind(StrEnum):
    """Invariant families for cyber-physical benchmark checks."""

    VARIABLE_EQUALS = "variable_equals"
    VARIABLE_BETWEEN = "variable_between"
    RELATION = "relation"
    TREND = "trend"
    MOVES_TOWARD = "moves_toward"
    REPLY_MATCHES_PROCESS_SNAPSHOT = "reply_matches_process_snapshot"


class InvariantComparison(StrEnum):
    LT = "lt"
    LE = "le"
    EQ = "eq"
    GE = "ge"
    GT = "gt"


class TrendDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class ProcessInvariant:
    """Declarative physical consistency rule for one benchmark step.

    The field set is intentionally compact and JSON-friendly. A rule uses only
    the fields that are meaningful for its `kind`, which keeps the schema
    extensible for future process backends and protocols.
    """

    invariant_id: str
    kind: ProcessInvariantKind
    variable_id: str | None = None
    value: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    left_variable: str | None = None
    right_variable: str | None = None
    right_value: float | None = None
    operator: InvariantComparison | None = None
    target_variable: str | None = None
    direction: TrendDirection | None = None
    tolerance: float = 1e-9
    reply_index: int | None = None
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "kind": self.kind.value,
            "variable_id": self.variable_id,
            "value": self.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "left_variable": self.left_variable,
            "right_variable": self.right_variable,
            "right_value": self.right_value,
            "operator": self.operator.value if self.operator else None,
            "target_variable": self.target_variable,
            "direction": self.direction.value if self.direction else None,
            "tolerance": self.tolerance,
            "reply_index": self.reply_index,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProcessInvariant:
        return cls(
            invariant_id=str(_required(data, "invariant_id")),
            kind=ProcessInvariantKind(str(_required(data, "kind"))),
            variable_id=_optional_str(data.get("variable_id")),
            value=_optional_float(data.get("value")),
            minimum=_optional_float(data.get("minimum")),
            maximum=_optional_float(data.get("maximum")),
            left_variable=_optional_str(data.get("left_variable")),
            right_variable=_optional_str(data.get("right_variable")),
            right_value=_optional_float(data.get("right_value")),
            operator=(
                InvariantComparison(str(data["operator"]))
                if data.get("operator") is not None
                else None
            ),
            target_variable=_optional_str(data.get("target_variable")),
            direction=(
                TrendDirection(str(data["direction"]))
                if data.get("direction") is not None
                else None
            ),
            tolerance=float(data.get("tolerance", 1e-9)),
            reply_index=(
                int(data["reply_index"]) if data.get("reply_index") is not None else None
            ),
            description=str(data.get("description", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class ProcessInvariantResult:
    invariant_id: str
    kind: ProcessInvariantKind
    passed: bool
    reason: str
    actual: object | None = None
    expected: object | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "kind": self.kind.value,
            "passed": self.passed,
            "reason": self.reason,
            "actual": self.actual,
            "expected": self.expected,
            "details": dict(self.details),
        }


class ProcessInvariantEvaluator:
    """Evaluate declarative invariants against process snapshots and replies."""

    def evaluate_many(
        self,
        invariants: Iterable[ProcessInvariant],
        *,
        context: PhysicalProcessContext | None,
        before_snapshot: ProcessSnapshot | None,
        after_snapshot: ProcessSnapshot | None,
        event: ICSEvent,
        reply_values: tuple[int, ...] | None,
    ) -> tuple[ProcessInvariantResult, ...]:
        return tuple(
            self.evaluate(
                invariant,
                context=context,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                event=event,
                reply_values=reply_values,
            )
            for invariant in invariants
        )

    def evaluate(
        self,
        invariant: ProcessInvariant,
        *,
        context: PhysicalProcessContext | None,
        before_snapshot: ProcessSnapshot | None,
        after_snapshot: ProcessSnapshot | None,
        event: ICSEvent,
        reply_values: tuple[int, ...] | None,
    ) -> ProcessInvariantResult:
        if context is None or after_snapshot is None:
            return _failed(invariant, "missing_process_context")
        try:
            if invariant.kind is ProcessInvariantKind.VARIABLE_EQUALS:
                return _variable_equals(invariant, after_snapshot)
            if invariant.kind is ProcessInvariantKind.VARIABLE_BETWEEN:
                return _variable_between(invariant, context, after_snapshot)
            if invariant.kind is ProcessInvariantKind.RELATION:
                return _relation(invariant, after_snapshot)
            if invariant.kind is ProcessInvariantKind.TREND:
                if before_snapshot is None:
                    return _failed(invariant, "missing_before_snapshot")
                return _trend(invariant, before_snapshot, after_snapshot)
            if invariant.kind is ProcessInvariantKind.MOVES_TOWARD:
                if before_snapshot is None:
                    return _failed(invariant, "missing_before_snapshot")
                return _moves_toward(invariant, before_snapshot, after_snapshot)
            if invariant.kind is ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT:
                return _reply_matches_process_snapshot(
                    invariant,
                    context,
                    event,
                    reply_values,
                )
        except (KeyError, TypeError, ValueError) as exc:
            return _failed(invariant, str(exc))
        return _failed(invariant, f"unsupported_invariant_kind:{invariant.kind}")


def _variable_equals(
    invariant: ProcessInvariant,
    snapshot: ProcessSnapshot,
) -> ProcessInvariantResult:
    variable_id = _required_variable(invariant)
    expected = _required_number(invariant.value, "value")
    actual = snapshot.read(variable_id)
    passed = _near(actual, expected, invariant.tolerance)
    return ProcessInvariantResult(
        invariant_id=invariant.invariant_id,
        kind=invariant.kind,
        passed=passed,
        reason="variable_equals" if passed else "variable_value_mismatch",
        actual=actual,
        expected=expected,
    )


def _variable_between(
    invariant: ProcessInvariant,
    context: PhysicalProcessContext,
    snapshot: ProcessSnapshot,
) -> ProcessInvariantResult:
    variable_id = _required_variable(invariant)
    variable = context.backend.variables.get(variable_id)
    lower = invariant.minimum
    upper = invariant.maximum
    if lower is None and variable is not None:
        lower = variable.minimum
    if upper is None and variable is not None:
        upper = variable.maximum
    if lower is None and upper is None:
        raise ValueError("variable_between requires minimum or maximum")
    actual = snapshot.read(variable_id)
    passed = True
    if lower is not None:
        passed = passed and actual >= lower - invariant.tolerance
    if upper is not None:
        passed = passed and actual <= upper + invariant.tolerance
    expected = {"minimum": lower, "maximum": upper}
    return ProcessInvariantResult(
        invariant_id=invariant.invariant_id,
        kind=invariant.kind,
        passed=passed,
        reason="variable_between" if passed else "variable_out_of_bounds",
        actual=actual,
        expected=expected,
    )


def _relation(
    invariant: ProcessInvariant,
    snapshot: ProcessSnapshot,
) -> ProcessInvariantResult:
    left_variable = _required_text(invariant.left_variable, "left_variable")
    operator = _required_operator(invariant)
    left_value = snapshot.read(left_variable)
    if invariant.right_variable is not None:
        right = snapshot.read(invariant.right_variable)
        expected: object = {
            "operator": operator.value,
            "right_variable": invariant.right_variable,
            "right_value": right,
        }
    else:
        right = _required_number(invariant.right_value, "right_value")
        expected = {"operator": operator.value, "right_value": right}
    passed = _compare(left_value, right, operator, invariant.tolerance)
    return ProcessInvariantResult(
        invariant_id=invariant.invariant_id,
        kind=invariant.kind,
        passed=passed,
        reason="relation_satisfied" if passed else "relation_violated",
        actual=left_value,
        expected=expected,
    )


def _trend(
    invariant: ProcessInvariant,
    before_snapshot: ProcessSnapshot,
    after_snapshot: ProcessSnapshot,
) -> ProcessInvariantResult:
    variable_id = _required_variable(invariant)
    direction = _required_direction(invariant)
    before = before_snapshot.read(variable_id)
    after = after_snapshot.read(variable_id)
    delta = after - before
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


def _moves_toward(
    invariant: ProcessInvariant,
    before_snapshot: ProcessSnapshot,
    after_snapshot: ProcessSnapshot,
) -> ProcessInvariantResult:
    variable_id = _required_variable(invariant)
    target_variable = _required_text(invariant.target_variable, "target_variable")
    before_value = before_snapshot.read(variable_id)
    after_value = after_snapshot.read(variable_id)
    target_value = after_snapshot.read(target_variable)
    before_distance = abs(before_value - target_value)
    after_distance = abs(after_value - target_value)
    passed = after_distance <= before_distance + invariant.tolerance
    return ProcessInvariantResult(
        invariant_id=invariant.invariant_id,
        kind=invariant.kind,
        passed=passed,
        reason="moves_toward_target" if passed else "moves_away_from_target",
        actual={
            "before": before_value,
            "after": after_value,
            "target": target_value,
            "before_distance": before_distance,
            "after_distance": after_distance,
        },
        expected={"target_variable": target_variable},
    )


def _reply_matches_process_snapshot(
    invariant: ProcessInvariant,
    context: PhysicalProcessContext,
    event: ICSEvent,
    reply_values: tuple[int, ...] | None,
) -> ProcessInvariantResult:
    if reply_values is None:
        return _failed(invariant, "missing_protocol_reply_values")
    expected_values = context.values_for_modbus_event(event)
    if expected_values is None:
        return _failed(invariant, "event_is_not_a_mapped_modbus_read")
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
            "reply_matches_process_snapshot"
            if passed
            else "reply_process_snapshot_mismatch"
        ),
        actual=actual,
        expected=expected,
    )


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


def _required(data: Mapping[str, Any], key: str) -> Any:
    if key not in data or data[key] is None:
        raise ValueError(f"missing required invariant field: {key}")
    return data[key]


def _optional_str(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _required_variable(invariant: ProcessInvariant) -> str:
    return _required_text(invariant.variable_id, "variable_id")


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
