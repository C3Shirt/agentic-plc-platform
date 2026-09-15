from __future__ import annotations

import ast
import math
import operator
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .base import (
    ProcessSnapshot,
    ProcessVariable,
    ReadOnlyProcessVariable,
    UnknownProcessVariable,
)


class FormulaExpressionError(ValueError):
    """Raised when a formula process expression is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class FormulaEquation:
    """One synchronous state-update equation for a process variable.

    The expression is evaluated against the pre-tick state plus `dt`, then
    written to `target` in the next state. Equations therefore do not depend on
    file order.
    """

    target: str
    expression: str
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FormulaEquation:
        return cls(
            target=str(payload["target"]),
            expression=str(payload["expression"]),
            minimum=_optional_float(payload.get("minimum")),
            maximum=_optional_float(payload.get("maximum")),
            description=str(payload.get("description", "")),
            metadata=dict(payload.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "expression": self.expression,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


class FormulaProcessBackend:
    """Generic equation-driven process backend for attacker-observable physics.

    This backend is intentionally lightweight: it is not a high-fidelity
    numerical simulator. It provides a safe, deterministic way to express the
    attacker-visible dynamics of a process slice through equations and bounds.
    More sophisticated physical-process agents can later target the same
    `ProcessBackend` contract.
    """

    def __init__(
        self,
        *,
        process_id: str,
        name: str,
        variables: Iterable[ProcessVariable],
        initial_state: Mapping[str, float],
        equations: Iterable[FormulaEquation],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.process_id = process_id
        self.name = name
        self._variables = {variable.variable_id: variable for variable in variables}
        if not self._variables:
            raise ValueError("formula backend requires at least one variable")
        self._initial_state = {
            variable_id: float(value)
            for variable_id, value in initial_state.items()
        }
        self._equations = tuple(equations)
        self._metadata = dict(metadata or {})
        self._revision = 0
        self._simulated_seconds = 0.0
        self._validate_definition()
        self._state = dict(self._initial_state)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FormulaProcessBackend:
        variables = tuple(_variable_from_dict(item) for item in payload["variables"])
        variable_defaults = {
            variable.variable_id: _initial_value_for_variable(variable)
            for variable in variables
        }
        for item in payload["variables"]:
            if isinstance(item, Mapping) and "initial_value" in item:
                variable_defaults[str(item["variable_id"])] = float(
                    item["initial_value"]
                )
        initial_state = {
            **variable_defaults,
            **{
                str(key): float(value)
                for key, value in dict(payload.get("initial_state", {})).items()
            },
        }
        return cls(
            process_id=str(payload["process_id"]),
            name=str(payload.get("name", "formula_process")),
            variables=variables,
            initial_state=initial_state,
            equations=tuple(
                FormulaEquation.from_dict(item)
                for item in payload.get("equations", ())
                if isinstance(item, Mapping)
            ),
            metadata=dict(payload.get("metadata", {})),
        )

    @property
    def variables(self) -> Mapping[str, ProcessVariable]:
        return dict(self._variables)

    @property
    def equations(self) -> tuple[FormulaEquation, ...]:
        return self._equations

    def reset(self) -> None:
        self._revision = 0
        self._simulated_seconds = 0.0
        self._state = dict(self._initial_state)

    def snapshot(self) -> ProcessSnapshot:
        measurements: dict[str, float] = {}
        manipulated_variables: dict[str, float] = {}
        setpoints: dict[str, float] = {}
        other_variables: dict[str, float] = {}

        for variable_id, variable in self._variables.items():
            value = self._state[variable_id]
            if variable.role == "measurement":
                measurements[variable_id] = value
            elif variable.role == "manipulated_variable":
                manipulated_variables[variable_id] = value
            elif variable.role == "setpoint":
                setpoints[variable_id] = value
            else:
                other_variables[variable_id] = value

        metadata = {
            **self._metadata,
            "backend_type": "formula",
            "equation_count": len(self._equations),
            "equations": [equation.to_dict() for equation in self._equations],
        }
        if other_variables:
            metadata["other_variables"] = other_variables
        return ProcessSnapshot(
            process_id=self.process_id,
            backend_name=self.name,
            revision=self._revision,
            simulated_seconds=self._simulated_seconds,
            measurements=measurements,
            manipulated_variables=manipulated_variables,
            setpoints=setpoints,
            metadata=metadata,
        )

    def read(self, variable_id: str) -> float:
        self._require_variable(variable_id)
        return self._state[variable_id]

    def write(self, variable_id: str, value: float) -> None:
        variable = self._require_variable(variable_id)
        if not variable.writable:
            raise ReadOnlyProcessVariable(variable_id)
        self._state[variable_id] = self._bounded_value(variable, float(value))
        self._revision += 1

    def write_internal(self, variable_id: str, value: float) -> None:
        """Update state from simulator/physical-process dynamics.

        External protocol writes must still use `write()` and are constrained to
        writable setpoints or manipulated variables. This hook is deliberately
        narrower: it preserves variable existence and bounds checks while
        allowing an internal process model/agent to evolve read-only
        measurements.
        """

        variable = self._require_variable(variable_id)
        self._state[variable_id] = self._bounded_value(variable, float(value))
        self._revision += 1

    def tick(self, seconds: float = 1.0) -> None:
        dt = float(seconds)
        if dt <= 0:
            raise ValueError("seconds must be positive")
        before = dict(self._state)
        after = dict(before)
        scope = {**before, "dt": dt}

        for equation in self._equations:
            variable = self._require_variable(equation.target)
            value = _evaluate_formula(equation.expression, scope)
            value = _bounded_number(
                equation.target,
                value,
                minimum=_coalesce(equation.minimum, variable.minimum),
                maximum=_coalesce(equation.maximum, variable.maximum),
            )
            after[equation.target] = value

        self._state = after
        self._simulated_seconds += dt
        self._revision += 1

    def _validate_definition(self) -> None:
        missing = set(self._variables) - set(self._initial_state)
        if missing:
            raise ValueError(
                f"formula backend missing initial values for variables: {sorted(missing)}"
            )
        extra = set(self._initial_state) - set(self._variables)
        if extra:
            raise ValueError(
                f"formula backend has initial values for unknown variables: {sorted(extra)}"
            )
        for variable in self._variables.values():
            self._bounded_value(variable, self._initial_state[variable.variable_id])
        allowed_names = set(self._variables) | {"dt"} | set(_ALLOWED_FUNCTIONS)
        for equation in self._equations:
            if equation.target not in self._variables:
                raise UnknownProcessVariable(equation.target)
            if (
                equation.minimum is not None
                and equation.maximum is not None
                and equation.minimum > equation.maximum
            ):
                raise ValueError(f"{equation.target} equation minimum exceeds maximum")
            _validate_expression(equation.expression, allowed_names)

    def _require_variable(self, variable_id: str) -> ProcessVariable:
        try:
            return self._variables[variable_id]
        except KeyError as exc:
            raise UnknownProcessVariable(variable_id) from exc

    def _bounded_value(self, variable: ProcessVariable, value: float) -> float:
        return _bounded_number(
            variable.variable_id,
            value,
            minimum=variable.minimum,
            maximum=variable.maximum,
        )


_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "ceil": math.ceil,
    "clamp": lambda value, low, high: min(max(value, low), high),
    "cos": math.cos,
    "exp": math.exp,
    "floor": math.floor,
    "log": math.log,
    "max": max,
    "min": min,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tanh": math.tanh,
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}

_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _evaluate_formula(expression: str, scope: Mapping[str, float]) -> float:
    parsed = ast.parse(expression, mode="eval")
    value = _eval_node(parsed.body, scope)
    return _finite_float("formula result", value)


def _validate_expression(expression: str, allowed_names: set[str]) -> None:
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise FormulaExpressionError(f"invalid formula syntax: {expression}") from exc
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise FormulaExpressionError(f"unknown formula name: {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
                raise FormulaExpressionError("formula calls may only use allowed functions")
            if node.keywords:
                raise FormulaExpressionError("formula function keywords are not allowed")
        if isinstance(
            node,
            (
                ast.Attribute,
                ast.Subscript,
                ast.List,
                ast.Tuple,
                ast.Dict,
                ast.Set,
                ast.Lambda,
                ast.ListComp,
                ast.DictComp,
                ast.SetComp,
                ast.GeneratorExp,
                ast.Await,
                ast.Yield,
                ast.NamedExpr,
            ),
        ):
            raise FormulaExpressionError(
                f"unsupported formula syntax: {type(node).__name__}"
            )


def _eval_node(node: ast.AST, scope: Mapping[str, float]) -> object:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise FormulaExpressionError("formula constants must be numeric or boolean")
    if isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        if node.id in _ALLOWED_FUNCTIONS:
            return _ALLOWED_FUNCTIONS[node.id]
        raise FormulaExpressionError(f"unknown formula name: {node.id}")
    if isinstance(node, ast.BinOp):
        try:
            op = _BIN_OPS[type(node.op)]
        except KeyError as exc:
            raise FormulaExpressionError(
                f"unsupported formula operator: {type(node.op).__name__}"
            ) from exc
        return op(_eval_node(node.left, scope), _eval_node(node.right, scope))
    if isinstance(node, ast.UnaryOp):
        try:
            op = _UNARY_OPS[type(node.op)]
        except KeyError as exc:
            raise FormulaExpressionError(
                f"unsupported formula unary operator: {type(node.op).__name__}"
            ) from exc
        return op(_eval_node(node.operand, scope))
    if isinstance(node, ast.IfExp):
        return (
            _eval_node(node.body, scope)
            if bool(_eval_node(node.test, scope))
            else _eval_node(node.orelse, scope)
        )
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: object = True
            for value_node in node.values:
                result = _eval_node(value_node, scope)
                if not bool(result):
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value_node in node.values:
                result = _eval_node(value_node, scope)
                if bool(result):
                    return result
            return result
        raise FormulaExpressionError(
            f"unsupported formula boolean operator: {type(node.op).__name__}"
        )
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, scope)
        for operator_node, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, scope)
            try:
                compare = _COMPARE_OPS[type(operator_node)]
            except KeyError as exc:
                raise FormulaExpressionError(
                    f"unsupported formula comparison: {type(operator_node).__name__}"
                ) from exc
            if not compare(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
            raise FormulaExpressionError("formula calls may only use allowed functions")
        func = _ALLOWED_FUNCTIONS[node.func.id]
        return func(*(_eval_node(argument, scope) for argument in node.args))
    raise FormulaExpressionError(f"unsupported formula syntax: {type(node).__name__}")


def _bounded_number(
    name: str,
    value: object,
    *,
    minimum: float | None,
    maximum: float | None,
) -> float:
    number = _finite_float(name, value)
    if minimum is not None and number < minimum:
        return float(minimum)
    if maximum is not None and number > maximum:
        return float(maximum)
    return number


def _finite_float(name: str, value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FormulaExpressionError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise FormulaExpressionError(f"{name} must be finite")
    return number


def _variable_from_dict(payload: Mapping[str, Any]) -> ProcessVariable:
    return ProcessVariable(
        variable_id=str(payload["variable_id"]),
        name=str(payload.get("name", payload["variable_id"])),
        role=str(payload.get("role", "measurement")),
        unit=None if payload.get("unit") is None else str(payload.get("unit")),
        minimum=_optional_float(payload.get("minimum")),
        maximum=_optional_float(payload.get("maximum")),
        writable=bool(payload.get("writable", False)),
        description=str(payload.get("description", "")),
        metadata=dict(payload.get("metadata", {})),
    )


def _initial_value_for_variable(variable: ProcessVariable) -> float:
    if variable.minimum is not None and variable.minimum > 0:
        return float(variable.minimum)
    if variable.maximum is not None and variable.maximum < 0:
        return float(variable.maximum)
    return 0.0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _coalesce(left: float | None, right: float | None) -> float | None:
    return left if left is not None else right
