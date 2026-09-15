from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from agentic_plc.contracts.actions import WorldPatch, WorldPatchOperation
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.processes import ProcessBackend, ProcessRegisterMap, ScenarioMapping
from agentic_plc.processes.base import ProcessSnapshot
from agentic_plc.world.registers import RegisterArea


@dataclass(frozen=True, slots=True)
class ExposedProcessPoint:
    variable_id: str
    role: str
    name: str
    value: float
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    protocol: str | None = None
    table: str | None = None
    address: int | None = None
    access: str = "read"
    scale: float = 1.0
    tag: str | None = None
    writable: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

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
        }


@dataclass(slots=True)
class PhysicalProcessContext:
    """Runtime process awareness passed to a generic honeypot agent.

    The context describes the currently active physical process and the PLC slice
    exposed to protocol adapters. It keeps TE, tank-pump, and future process
    types behind one shape that planners can reason over.
    """

    backend: ProcessBackend
    scenario: ScenarioMapping | None = None
    register_map: ProcessRegisterMap | None = None
    protocol: str = "modbus"
    max_prompt_points: int = 32

    @property
    def process_id(self) -> str:
        return self.backend.process_id

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def snapshot(self) -> ProcessSnapshot:
        return self.backend.snapshot()

    def exposed_points(self) -> list[ExposedProcessPoint]:
        snapshot = self.snapshot()
        variables = self.backend.variables
        if self.scenario is None:
            return [
                ExposedProcessPoint(
                    variable_id=variable_id,
                    role=variable.role,
                    name=variable.name,
                    value=snapshot.read(variable_id),
                    unit=variable.unit,
                    minimum=variable.minimum,
                    maximum=variable.maximum,
                    writable=variable.writable,
                    metadata=variable.metadata,
                )
                for variable_id, variable in variables.items()
            ]

        points: list[ExposedProcessPoint] = []
        for mapping in self.scenario.variables_for_protocol(self.protocol):
            variable = variables[mapping.variable_id]
            points.append(
                ExposedProcessPoint(
                    variable_id=mapping.variable_id,
                    role=variable.role,
                    name=variable.name,
                    value=snapshot.read(mapping.variable_id),
                    unit=variable.unit,
                    minimum=variable.minimum,
                    maximum=variable.maximum,
                    protocol=mapping.protocol,
                    table=mapping.table,
                    address=mapping.address,
                    access=mapping.access,
                    scale=mapping.scale,
                    tag=mapping.tag,
                    writable=variable.writable and "write" in mapping.access,
                    metadata={**dict(variable.metadata), **dict(mapping.metadata)},
                )
            )
        return points

    def writable_variable_ids(self) -> list[str]:
        return [
            point.variable_id
            for point in self.exposed_points()
            if point.writable
        ]

    def values_for_modbus_event(self, event: ICSEvent) -> list[int] | None:
        if self.register_map is None:
            return None
        area = _register_area_for_event(event)
        if area is None:
            return None
        if event.address is None or event.count is None:
            return None
        return self.register_map.read(area, event.address, event.count)

    def world_patch_for_modbus_write_event(self, event: ICSEvent) -> WorldPatch | None:
        """Translate a Modbus write into a validated process-variable patch.

        This method does not mutate the backend. It only uses the scenario's
        protocol mapping to decode attacker-facing cells into canonical process
        variables and engineering values. The controller still applies the
        returned patch through `ProcessPatchApplier`.
        """

        if self.register_map is None:
            return None
        area = _register_area_for_event(event)
        if area not in {RegisterArea.COILS, RegisterArea.HOLDING_REGISTERS}:
            return None
        if event.address is None:
            return None
        values = _write_values_for_event(event)
        if not values:
            return None
        plans = self.register_map.preview_write_many(area, event.address, values)
        if not plans:
            return None
        snapshot = self.snapshot()
        return WorldPatch(
            actor_id=event.actor_id or f"ip:{event.source_ip}",
            reason=(
                "Apply protocol write to mapped physical-process variables "
                "before acknowledging the write."
            ),
            ttl_seconds=60,
            operations=[
                WorldPatchOperation(
                    path=plan.variable_id,
                    value=plan.engineering_value,
                    reason=(
                        f"Decoded {plan.area.value}:{plan.address} "
                        f"requested={plan.requested_value}."
                    ),
                )
                for plan in plans
            ],
            metadata={
                "base_revision": snapshot.revision,
                "process_id": snapshot.process_id,
                "backend_name": snapshot.backend_name,
                "source_protocol": "modbus",
                "transaction_id": event.transaction_id,
                "unit_id": event.unit_id,
                "operation": event.operation,
                "address": event.address,
                "count": event.count,
            },
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        exposed = self.exposed_points()[: self.max_prompt_points]
        return {
            "process_id": self.process_id,
            "backend_name": self.backend_name,
            "scenario_id": self.scenario.scenario_id if self.scenario else None,
            "plc_area": self.scenario.plc_area if self.scenario else None,
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
            "exposed_points": [point.to_prompt_dict() for point in exposed],
            "writable_variable_ids": self.writable_variable_ids(),
        }


def _register_area_for_event(event: ICSEvent) -> RegisterArea | None:
    function_code = event.metadata.get("function_code")
    if function_code is None:
        if "holding" in event.operation:
            return RegisterArea.HOLDING_REGISTERS
        if "input" in event.operation:
            return RegisterArea.INPUT_REGISTERS
        if "coil" in event.operation:
            return RegisterArea.COILS
        if "discrete" in event.operation:
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


def _write_values_for_event(event: ICSEvent) -> list[int | bool] | None:
    if event.requested_value is None:
        return None
    if isinstance(event.requested_value, list):
        return list(event.requested_value)
    if isinstance(event.requested_value, tuple):
        return list(event.requested_value)
    return [event.requested_value]
