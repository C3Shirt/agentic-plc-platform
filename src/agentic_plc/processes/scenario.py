from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProtocolPointMapping:
    """Mapping from one process variable to one protocol-facing point."""

    variable_id: str
    protocol: str
    table: str
    address: int
    access: str
    data_type: str = "uint16"
    scale: float = 1.0
    tag: str | None = None
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProtocolPointMapping:
        return cls(
            variable_id=str(payload["variable_id"]),
            protocol=str(payload.get("protocol", "modbus")),
            table=str(payload["table"]),
            address=int(payload["address"]),
            access=str(payload.get("access", "read")),
            data_type=str(payload.get("data_type", "uint16")),
            scale=float(payload.get("scale", 1.0)),
            tag=payload.get("tag"),
            description=str(payload.get("description", "")),
            metadata=dict(payload.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_id": self.variable_id,
            "protocol": self.protocol,
            "table": self.table,
            "address": self.address,
            "access": self.access,
            "data_type": self.data_type,
            "scale": self.scale,
            "tag": self.tag,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ScenarioMapping:
    """Declarative PLC slice over a physical process backend."""

    scenario_id: str
    process_id: str
    backend_type: str
    plc_area: str
    description: str
    points: tuple[ProtocolPointMapping, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> ScenarioMapping:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScenarioMapping:
        backend = payload.get("backend", {})
        process_id = str(payload.get("process_id") or backend.get("process_id"))
        backend_type = str(payload.get("backend_type") or backend.get("type"))
        return cls(
            scenario_id=str(payload["scenario_id"]),
            process_id=process_id,
            backend_type=backend_type,
            plc_area=str(payload.get("plc_area", "")),
            description=str(payload.get("description", "")),
            points=tuple(
                ProtocolPointMapping.from_dict(point)
                for point in payload.get("points", [])
            ),
            metadata=dict(payload.get("metadata", {})),
        )

    def variables_for_protocol(self, protocol: str) -> tuple[ProtocolPointMapping, ...]:
        return tuple(point for point in self.points if point.protocol == protocol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "process_id": self.process_id,
            "backend_type": self.backend_type,
            "plc_area": self.plc_area,
            "description": self.description,
            "points": [point.to_dict() for point in self.points],
            "metadata": dict(self.metadata),
        }
