from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentic_plc.processes import (
    ProcessVariable,
    ScenarioMapping,
    TraceProcessBackend,
)
from agentic_plc.processes.scenario import ProtocolPointMapping
from agentic_plc.world.registers import RegisterArea

from .modbus_trace import ModbusTraceEvent


@dataclass(frozen=True, slots=True)
class LearnedVariableSample:
    timestamp: float
    variable_id: str
    value: float
    source: str
    table: str
    address: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "variable_id": self.variable_id,
            "value": self.value,
            "source": self.source,
            "table": self.table,
            "address": self.address,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LearnedVariableSample:
        return cls(
            timestamp=float(payload["timestamp"]),
            variable_id=str(payload["variable_id"]),
            value=float(payload["value"]),
            source=str(payload.get("source", "unknown")),
            table=str(payload["table"]),
            address=int(payload["address"]),
        )


@dataclass(frozen=True, slots=True)
class LearnedResponseExample:
    key: str
    request_hex: str
    response_hex: str
    operation: str
    table: str
    address: int | None
    count: int | None
    exception_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "request_hex": self.request_hex,
            "response_hex": self.response_hex,
            "operation": self.operation,
            "table": self.table,
            "address": self.address,
            "count": self.count,
            "exception_code": self.exception_code,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LearnedResponseExample:
        return cls(
            key=str(payload["key"]),
            request_hex=str(payload["request_hex"]),
            response_hex=str(payload["response_hex"]),
            operation=str(payload["operation"]),
            table=str(payload["table"]),
            address=_optional_int(payload.get("address")),
            count=_optional_int(payload.get("count")),
            exception_code=_optional_int(payload.get("exception_code")),
        )


@dataclass(frozen=True, slots=True)
class LearnedWriteEffect:
    timestamp: float
    operation: str
    table: str
    address: int
    request_values: tuple[int, ...]
    changed_variables: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "table": self.table,
            "address": self.address,
            "request_values": list(self.request_values),
            "changed_variables": dict(self.changed_variables),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LearnedWriteEffect:
        return cls(
            timestamp=float(payload["timestamp"]),
            operation=str(payload["operation"]),
            table=str(payload["table"]),
            address=int(payload["address"]),
            request_values=tuple(int(item) for item in payload.get("request_values", ())),
            changed_variables={
                str(key): float(value)
                for key, value in dict(payload.get("changed_variables", {})).items()
            },
        )


@dataclass(frozen=True, slots=True)
class LearnedProcessModel:
    """Trace-derived model that can seed a honeypot physical backend or RAG KB."""

    scenario_id: str
    process_id: str
    backend_type: str
    observed_operation_counts: Mapping[str, int]
    variable_samples: Mapping[str, tuple[LearnedVariableSample, ...]]
    response_examples: tuple[LearnedResponseExample, ...]
    write_effects: tuple[LearnedWriteEffect, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "process_id": self.process_id,
            "backend_type": self.backend_type,
            "observed_operation_counts": dict(self.observed_operation_counts),
            "variable_samples": {
                variable_id: [sample.to_dict() for sample in samples]
                for variable_id, samples in self.variable_samples.items()
            },
            "response_examples": [
                example.to_dict() for example in self.response_examples
            ],
            "write_effects": [effect.to_dict() for effect in self.write_effects],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LearnedProcessModel:
        samples_payload = payload.get("variable_samples", {})
        if not isinstance(samples_payload, Mapping):
            raise ValueError("variable_samples must be an object")
        return cls(
            scenario_id=str(payload["scenario_id"]),
            process_id=str(payload["process_id"]),
            backend_type=str(payload.get("backend_type", "learned_trace")),
            observed_operation_counts={
                str(key): int(value)
                for key, value in dict(payload.get("observed_operation_counts", {})).items()
            },
            variable_samples={
                str(variable_id): tuple(
                    LearnedVariableSample.from_dict(item)
                    for item in samples
                    if isinstance(item, Mapping)
                )
                for variable_id, samples in samples_payload.items()
            },
            response_examples=tuple(
                LearnedResponseExample.from_dict(item)
                for item in payload.get("response_examples", ())
                if isinstance(item, Mapping)
            ),
            write_effects=tuple(
                LearnedWriteEffect.from_dict(item)
                for item in payload.get("write_effects", ())
                if isinstance(item, Mapping)
            ),
            metadata=dict(payload.get("metadata", {})),
        )

    def to_trace_backend(
        self,
        variables: Iterable[ProcessVariable] | None = None,
        *,
        loop: bool = False,
    ) -> TraceProcessBackend:
        variable_defs = tuple(variables or _infer_variables(self.variable_samples))
        variable_ids = tuple(variable.variable_id for variable in variable_defs)
        time_seconds = _trace_times(self.variable_samples)
        series: dict[str, list[float]] = {}
        for variable in variable_defs:
            series[variable.variable_id] = _series_for_variable(
                variable.variable_id,
                variable,
                time_seconds,
                self.variable_samples.get(variable.variable_id, ()),
            )
        return TraceProcessBackend(
            process_id=self.process_id,
            name=f"{self.scenario_id}_learned_trace",
            time_seconds=time_seconds,
            variables=variable_defs,
            series={variable_id: series[variable_id] for variable_id in variable_ids},
            metadata={
                **dict(self.metadata),
                "backend_type": self.backend_type,
                "learned_response_example_count": len(self.response_examples),
                "learned_write_effect_count": len(self.write_effects),
            },
            loop=loop,
        )


def learn_process_model(
    scenario: ScenarioMapping,
    interactions: Iterable[ModbusTraceEvent],
    *,
    variables: Iterable[ProcessVariable] | None = None,
    write_effect_window_seconds: float = 5.0,
) -> LearnedProcessModel:
    """Learn a lightweight physical-process model from normalized Modbus pairs."""

    events = tuple(sorted(interactions, key=lambda event: event.timestamp))
    point_index = _point_index(scenario)
    samples: dict[str, list[LearnedVariableSample]] = defaultdict(list)
    response_examples: list[LearnedResponseExample] = []
    operation_counts = Counter(event.operation for event in events)

    for event in events:
        response_examples.append(_response_example(event))
        if event.exception_code is not None or event.address is None:
            continue
        if event.is_read:
            _collect_read_samples(event, point_index, samples)
        elif event.is_write:
            _collect_write_samples(event, point_index, samples)

    write_effects = _infer_write_effects(
        events,
        samples,
        window_seconds=write_effect_window_seconds,
    )
    variable_defs = tuple(variables or _infer_variables(samples))
    return LearnedProcessModel(
        scenario_id=scenario.scenario_id,
        process_id=scenario.process_id,
        backend_type="learned_modbus_trace",
        observed_operation_counts=dict(operation_counts),
        variable_samples={
            variable.variable_id: tuple(
                sorted(samples.get(variable.variable_id, ()), key=lambda item: item.timestamp)
            )
            for variable in variable_defs
        },
        response_examples=tuple(response_examples),
        write_effects=tuple(write_effects),
        metadata={
            "source": "modbus_request_response_trace",
            "event_count": len(events),
            "mapped_variable_count": len(variable_defs),
            "scenario_backend_type": scenario.backend_type,
        },
    )


def _collect_read_samples(
    event: ModbusTraceEvent,
    point_index: Mapping[tuple[str, int], ProtocolPointMapping],
    samples: dict[str, list[LearnedVariableSample]],
) -> None:
    if event.address is None:
        return
    for offset, raw_value in enumerate(event.response_values):
        point = point_index.get((event.table, event.address + offset))
        if point is None:
            continue
        samples[point.variable_id].append(
            LearnedVariableSample(
                timestamp=event.timestamp,
                variable_id=point.variable_id,
                value=_decode_point_value(point, raw_value),
                source=event.operation,
                table=event.table,
                address=event.address + offset,
            )
        )


def _collect_write_samples(
    event: ModbusTraceEvent,
    point_index: Mapping[tuple[str, int], ProtocolPointMapping],
    samples: dict[str, list[LearnedVariableSample]],
) -> None:
    if event.address is None:
        return
    for offset, raw_value in enumerate(event.request_values):
        point = point_index.get((event.table, event.address + offset))
        if point is None:
            continue
        samples[point.variable_id].append(
            LearnedVariableSample(
                timestamp=event.timestamp,
                variable_id=point.variable_id,
                value=_decode_point_value(point, raw_value),
                source=event.operation,
                table=event.table,
                address=event.address + offset,
            )
        )


def _infer_write_effects(
    events: tuple[ModbusTraceEvent, ...],
    samples: Mapping[str, list[LearnedVariableSample]],
    *,
    window_seconds: float,
) -> tuple[LearnedWriteEffect, ...]:
    effects: list[LearnedWriteEffect] = []
    for event in events:
        if not event.is_write or event.address is None:
            continue
        before = _latest_values_before(samples, event.timestamp)
        after = _first_changed_values_after(
            samples,
            event.timestamp,
            before=before,
            window_seconds=window_seconds,
        )
        effects.append(
            LearnedWriteEffect(
                timestamp=event.timestamp,
                operation=event.operation,
                table=event.table,
                address=event.address,
                request_values=event.request_values,
                changed_variables=after,
            )
        )
    return tuple(effects)


def _latest_values_before(
    samples: Mapping[str, list[LearnedVariableSample]],
    timestamp: float,
) -> dict[str, float]:
    latest: dict[str, float] = {}
    for variable_id, variable_samples in samples.items():
        for sample in sorted(variable_samples, key=lambda item: item.timestamp):
            if sample.timestamp > timestamp:
                break
            latest[variable_id] = sample.value
    return latest


def _first_changed_values_after(
    samples: Mapping[str, list[LearnedVariableSample]],
    timestamp: float,
    *,
    before: Mapping[str, float],
    window_seconds: float,
) -> dict[str, float]:
    changed: dict[str, float] = {}
    deadline = timestamp + window_seconds
    for variable_id, variable_samples in samples.items():
        for sample in sorted(variable_samples, key=lambda item: item.timestamp):
            if sample.timestamp <= timestamp:
                continue
            if sample.timestamp > deadline:
                break
            if before.get(variable_id) != sample.value:
                changed[variable_id] = sample.value
                break
    return changed


def _response_example(event: ModbusTraceEvent) -> LearnedResponseExample:
    suffix = "exception" if event.exception_code is not None else "normal"
    key = f"{event.operation}:{event.table}:{event.address}:{event.count}:{suffix}"
    return LearnedResponseExample(
        key=key,
        request_hex=event.request_hex,
        response_hex=event.response_hex,
        operation=event.operation,
        table=event.table,
        address=event.address,
        count=event.count,
        exception_code=event.exception_code,
    )


def _point_index(
    scenario: ScenarioMapping,
) -> dict[tuple[str, int], ProtocolPointMapping]:
    return {
        (RegisterArea(point.table).value, point.address): point
        for point in scenario.variables_for_protocol("modbus")
    }


def _decode_point_value(point: ProtocolPointMapping, raw_value: int) -> float:
    if point.data_type == "bool":
        return float(bool(raw_value))
    if point.scale == 0:
        return float(raw_value)
    return float(raw_value) / float(point.scale)


def _infer_variables(
    samples: Mapping[str, Iterable[LearnedVariableSample]],
) -> tuple[ProcessVariable, ...]:
    variables: list[ProcessVariable] = []
    for variable_id in sorted(samples):
        variables.append(
            ProcessVariable(
                variable_id=variable_id,
                name=variable_id,
                role="measurement",
            )
        )
    return tuple(variables)


def _trace_times(
    samples: Mapping[str, tuple[LearnedVariableSample, ...]],
) -> list[float]:
    times = sorted({sample.timestamp for variable_samples in samples.values() for sample in variable_samples})
    return times or [0.0]


def _series_for_variable(
    variable_id: str,
    variable: ProcessVariable,
    time_seconds: list[float],
    samples: tuple[LearnedVariableSample, ...],
) -> list[float]:
    default = _default_value(variable)
    value_at_time: dict[float, float] = {}
    for sample in sorted(samples, key=lambda item: item.timestamp):
        value_at_time[sample.timestamp] = sample.value
    series: list[float] = []
    current = default
    for timestamp in time_seconds:
        if timestamp in value_at_time:
            current = value_at_time[timestamp]
        series.append(current)
    return series


def _default_value(variable: ProcessVariable) -> float:
    if variable.minimum is not None and variable.minimum > 0:
        return float(variable.minimum)
    if variable.maximum is not None and variable.maximum < 0:
        return float(variable.maximum)
    return 0.0


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
