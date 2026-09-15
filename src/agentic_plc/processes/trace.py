from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .base import (
    ProcessSnapshot,
    ProcessVariable,
    ReadOnlyProcessVariable,
    UnknownProcessVariable,
)


def load_whitespace_matrix(path: Path) -> list[list[float]]:
    rows: list[list[float]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append([float(part) for part in stripped.split()])
        except ValueError as exc:
            raise ValueError(f"invalid numeric value in {path}:{line_number}") from exc
    if not rows:
        raise ValueError(f"trace file has no rows: {path}")
    return rows


class TraceProcessBackend:
    """Generic backend for replaying sampled physical-process traces.

    It is intentionally simulator-agnostic: a TE IDV run, a water-treatment log,
    or an FMU/container export can all be normalized into time-series columns.
    Writes are stored as bounded runtime overrides so attacker interactions can
    change the PLC-facing state without mutating the original trace files.
    """

    def __init__(
        self,
        *,
        process_id: str,
        name: str,
        time_seconds: Sequence[float],
        variables: Iterable[ProcessVariable],
        series: Mapping[str, Sequence[float]],
        metadata: Mapping[str, Any] | None = None,
        loop: bool = False,
    ) -> None:
        if not time_seconds:
            raise ValueError("time_seconds must not be empty")
        if any(time < 0 for time in time_seconds):
            raise ValueError("time_seconds must be non-negative")
        if any(left > right for left, right in zip(time_seconds, time_seconds[1:])):
            raise ValueError("time_seconds must be monotonic")

        self.process_id = process_id
        self.name = name
        self._time_seconds = tuple(float(time) for time in time_seconds)
        self._variables = {variable.variable_id: variable for variable in variables}
        self._series = {
            variable_id: tuple(float(value) for value in values)
            for variable_id, values in series.items()
        }
        self._metadata = dict(metadata or {})
        self._loop = loop
        self._revision = 0
        self._row_index = 0
        self._simulated_seconds = self._time_seconds[0]
        self._overrides: dict[str, float] = {}
        self._validate_series()

    @property
    def variables(self) -> Mapping[str, ProcessVariable]:
        return dict(self._variables)

    def reset(self) -> None:
        self._revision = 0
        self._row_index = 0
        self._simulated_seconds = self._time_seconds[0]
        self._overrides.clear()

    def snapshot(self) -> ProcessSnapshot:
        measurements: dict[str, float] = {}
        manipulated_variables: dict[str, float] = {}
        setpoints: dict[str, float] = {}

        for variable_id, variable in self._variables.items():
            value = self._current_value(variable_id)
            if variable.role == "measurement":
                measurements[variable_id] = value
            elif variable.role == "manipulated_variable":
                manipulated_variables[variable_id] = value
            elif variable.role == "setpoint":
                setpoints[variable_id] = value

        metadata = {
            **self._metadata,
            "row_index": self._row_index,
            "row_count": len(self._time_seconds),
            "overrides": dict(self._overrides),
        }
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
        return self._current_value(variable_id)

    def write(self, variable_id: str, value: float) -> None:
        variable = self._require_variable(variable_id)
        if not variable.writable:
            raise ReadOnlyProcessVariable(variable_id)
        numeric_value = float(value)
        if variable.minimum is not None and numeric_value < variable.minimum:
            raise ValueError(f"{variable_id} below minimum {variable.minimum}")
        if variable.maximum is not None and numeric_value > variable.maximum:
            raise ValueError(f"{variable_id} above maximum {variable.maximum}")
        self._overrides[variable_id] = numeric_value
        self._revision += 1

    def write_internal(self, variable_id: str, value: float) -> None:
        """Set a runtime override from simulator/physical-process dynamics."""

        variable = self._require_variable(variable_id)
        numeric_value = float(value)
        if variable.minimum is not None and numeric_value < variable.minimum:
            raise ValueError(f"{variable_id} below minimum {variable.minimum}")
        if variable.maximum is not None and numeric_value > variable.maximum:
            raise ValueError(f"{variable_id} above maximum {variable.maximum}")
        self._overrides[variable_id] = numeric_value
        self._revision += 1

    def tick(self, seconds: float = 1.0) -> None:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        target = self._simulated_seconds + float(seconds)
        end = self._time_seconds[-1]
        start = self._time_seconds[0]

        if self._loop and end > start:
            span = end - start
            target = start + ((target - start) % span)
        else:
            target = min(target, end)

        self._simulated_seconds = target
        self._row_index = max(0, bisect_right(self._time_seconds, target) - 1)
        self._revision += 1

    def _validate_series(self) -> None:
        expected = len(self._time_seconds)
        missing = set(self._variables) - set(self._series)
        if missing:
            raise ValueError(f"missing trace series for variables: {sorted(missing)}")
        extra = set(self._series) - set(self._variables)
        if extra:
            raise ValueError(f"trace series has undeclared variables: {sorted(extra)}")
        for variable_id, values in self._series.items():
            if len(values) != expected:
                raise ValueError(
                    f"{variable_id} has {len(values)} rows; expected {expected}"
                )

    def _require_variable(self, variable_id: str) -> ProcessVariable:
        try:
            return self._variables[variable_id]
        except KeyError as exc:
            raise UnknownProcessVariable(variable_id) from exc

    def _current_value(self, variable_id: str) -> float:
        self._require_variable(variable_id)
        if variable_id in self._overrides:
            return self._overrides[variable_id]
        return self._series[variable_id][self._row_index]
