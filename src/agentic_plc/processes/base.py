from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


class UnknownProcessVariable(KeyError):
    """Raised when a backend does not expose a requested process variable."""


class ReadOnlyProcessVariable(ValueError):
    """Raised when a write targets a read-only process variable."""


@dataclass(frozen=True, slots=True)
class ProcessVariable:
    """One canonical physical-process variable exposed by a backend."""

    variable_id: str
    name: str
    role: str
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    writable: bool = False
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_id": self.variable_id,
            "name": self.name,
            "role": self.role,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "writable": self.writable,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """Revisioned point-in-time view of a physical-process backend."""

    process_id: str
    backend_name: str
    revision: int
    simulated_seconds: float
    measurements: Mapping[str, float] = field(default_factory=dict)
    manipulated_variables: Mapping[str, float] = field(default_factory=dict)
    setpoints: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def read(self, variable_id: str) -> float:
        for group in (
            self.measurements,
            self.manipulated_variables,
            self.setpoints,
        ):
            if variable_id in group:
                return float(group[variable_id])
        raise UnknownProcessVariable(variable_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "backend_name": self.backend_name,
            "revision": self.revision,
            "simulated_seconds": self.simulated_seconds,
            "measurements": dict(self.measurements),
            "manipulated_variables": dict(self.manipulated_variables),
            "setpoints": dict(self.setpoints),
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class ProcessBackend(Protocol):
    """Common contract for deterministic, trace, or external simulators."""

    process_id: str
    name: str

    @property
    def variables(self) -> Mapping[str, ProcessVariable]:
        ...

    def reset(self) -> None:
        ...

    def snapshot(self) -> ProcessSnapshot:
        ...

    def read(self, variable_id: str) -> float:
        ...

    def write(self, variable_id: str, value: float) -> None:
        ...

    def tick(self, seconds: float = 1.0) -> None:
        ...
