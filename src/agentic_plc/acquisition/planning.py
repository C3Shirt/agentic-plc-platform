from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agentic_plc.processes import ProcessVariable, ProtocolPointMapping, ScenarioMapping


@dataclass(frozen=True, slots=True)
class SceneComponentPlan:
    """One planned physical component in a scenario acquisition workflow."""

    component_id: str
    kind: str
    description: str
    variable_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "kind": self.kind,
            "description": self.description,
            "variable_ids": list(self.variable_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SceneComponentPlan:
        return cls(
            component_id=str(payload["component_id"]),
            kind=str(payload["kind"]),
            description=str(payload.get("description", "")),
            variable_ids=tuple(str(item) for item in payload.get("variable_ids", ())),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class PLCPointPlan:
    """Planned link between a process variable and a PLC/protocol point."""

    variable_id: str
    name: str
    role: str
    table: str
    address: int
    access: str
    data_type: str = "bool"
    scale: float = 1.0
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    writable: bool = False
    protocol: str = "modbus"
    tag: str | None = None
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_process_variable(self) -> ProcessVariable:
        return ProcessVariable(
            variable_id=self.variable_id,
            name=self.name,
            role=self.role,
            unit=self.unit,
            minimum=self.minimum,
            maximum=self.maximum,
            writable=self.writable,
            description=self.description,
            metadata=dict(self.metadata),
        )

    def to_protocol_point(self) -> ProtocolPointMapping:
        return ProtocolPointMapping(
            variable_id=self.variable_id,
            protocol=self.protocol,
            table=self.table,
            address=self.address,
            access=self.access,
            data_type=self.data_type,
            scale=self.scale,
            tag=self.tag,
            description=self.description,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_id": self.variable_id,
            "name": self.name,
            "role": self.role,
            "table": self.table,
            "address": self.address,
            "access": self.access,
            "data_type": self.data_type,
            "scale": self.scale,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "writable": self.writable,
            "protocol": self.protocol,
            "tag": self.tag,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PLCPointPlan:
        return cls(
            variable_id=str(payload["variable_id"]),
            name=str(payload.get("name", payload["variable_id"])),
            role=str(payload.get("role", "measurement")),
            table=str(payload["table"]),
            address=int(payload["address"]),
            access=str(payload.get("access", "read")),
            data_type=str(payload.get("data_type", "uint16")),
            scale=float(payload.get("scale", 1.0)),
            unit=None if payload.get("unit") is None else str(payload.get("unit")),
            minimum=_optional_float(payload.get("minimum")),
            maximum=_optional_float(payload.get("maximum")),
            writable=bool(payload.get("writable", False)),
            protocol=str(payload.get("protocol", "modbus")),
            tag=payload.get("tag"),
            description=str(payload.get("description", "")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class PLCProgramPlan:
    """A generated PLC program outline associated with an acquisition plan."""

    name: str
    language: str
    code: str
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "language": self.language,
            "code": self.code,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PLCProgramPlan:
        return cls(
            name=str(payload["name"]),
            language=str(payload.get("language", "structured_text")),
            code=str(payload.get("code", "")),
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True, slots=True)
class ScenarioAcquisitionPlan:
    """Artifact produced by the physical-process-aware planning stage.

    This is the bridge between a human-level scene description and concrete
    PLC/HMI/honeypot materials: components, process variables, Modbus points,
    and a PLC program skeleton. The execution/acquisition stage can then use
    this plan to configure CODESYS manually or drive a local simulator.
    """

    plan_id: str
    scenario_id: str
    process_id: str
    backend_type: str
    plc_area: str
    description: str
    components: tuple[SceneComponentPlan, ...]
    points: tuple[PLCPointPlan, ...]
    plc_states: tuple[str, ...] = ()
    plc_program: PLCProgramPlan | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def process_variables(self) -> tuple[ProcessVariable, ...]:
        return tuple(point.to_process_variable() for point in self.points)

    def scenario_mapping(self) -> ScenarioMapping:
        return ScenarioMapping(
            scenario_id=self.scenario_id,
            process_id=self.process_id,
            backend_type=self.backend_type,
            plc_area=self.plc_area,
            description=self.description,
            points=tuple(point.to_protocol_point() for point in self.points),
            metadata={
                **dict(self.metadata),
                "acquisition_plan_id": self.plan_id,
                "plc_states": list(self.plc_states),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "scenario_id": self.scenario_id,
            "process_id": self.process_id,
            "backend_type": self.backend_type,
            "plc_area": self.plc_area,
            "description": self.description,
            "components": [component.to_dict() for component in self.components],
            "points": [point.to_dict() for point in self.points],
            "plc_states": list(self.plc_states),
            "plc_program": (
                None if self.plc_program is None else self.plc_program.to_dict()
            ),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScenarioAcquisitionPlan:
        program_payload = payload.get("plc_program")
        return cls(
            plan_id=str(payload["plan_id"]),
            scenario_id=str(payload["scenario_id"]),
            process_id=str(payload["process_id"]),
            backend_type=str(payload["backend_type"]),
            plc_area=str(payload.get("plc_area", "")),
            description=str(payload.get("description", "")),
            components=tuple(
                SceneComponentPlan.from_dict(item)
                for item in payload.get("components", ())
                if isinstance(item, Mapping)
            ),
            points=tuple(
                PLCPointPlan.from_dict(item)
                for item in payload.get("points", ())
                if isinstance(item, Mapping)
            ),
            plc_states=tuple(str(item) for item in payload.get("plc_states", ())),
            plc_program=(
                PLCProgramPlan.from_dict(program_payload)
                if isinstance(program_payload, Mapping)
                else None
            ),
            metadata=dict(payload.get("metadata", {})),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> ScenarioAcquisitionPlan:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def write_artifacts(self, directory: str | Path) -> dict[str, Path]:
        output_dir = Path(directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "acquisition_plan": output_dir / "acquisition_plan.json",
            "scenario": output_dir / "scenario.json",
        }
        paths["acquisition_plan"].write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths["scenario"].write_text(
            json.dumps(self.scenario_mapping().to_dict(), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        if self.plc_program is not None:
            paths["plc_program"] = output_dir / f"{self.plc_program.name}.st"
            paths["plc_program"].write_text(
                self.plc_program.code.rstrip() + "\n",
                encoding="utf-8",
            )
        return paths


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
