from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agentic_plc.contracts.events import Intent
from agentic_plc.world.registers import RegisterAccessError, RegisterArea, RegisterWrite

from .base import ProcessBackend
from .scenario import ProtocolPointMapping, ScenarioMapping


class ProcessRegisterWriteError(RegisterAccessError):
    """Raised when a process-mapped register write cannot be represented safely."""


class ProcessRegisterReadOnlyError(ProcessRegisterWriteError):
    """Raised when a protocol write targets a read-only process variable."""


@dataclass(frozen=True, slots=True)
class ProcessRegisterWritePlan:
    """A non-mutating interpretation of one protocol write.

    The plan bridges protocol-facing cells and canonical process variables. It
    lets an agent/controller decide whether to accept a Modbus write before the
    physical-process backend is mutated.
    """

    area: RegisterArea
    address: int
    variable_id: str
    requested_value: int
    engineering_value: float


class ProcessRegisterMap:
    """Expose a scenario-mapped process backend as Modbus-style registers."""

    def __init__(
        self,
        backend: ProcessBackend,
        scenario: ScenarioMapping,
        *,
        protocol: str = "modbus",
    ) -> None:
        self.backend = backend
        self.scenario = scenario
        self.protocol = protocol
        self._points = self._index_points(scenario.variables_for_protocol(protocol))
        self._validate_backend_variables()

    def block_size(self, area: RegisterArea | str) -> int:
        area = RegisterArea(area)
        points = self._points.get(area, {})
        if not points:
            return 0
        return max(points) + 1

    def read(self, area: RegisterArea | str, address: int, count: int = 1) -> list[int]:
        area = RegisterArea(area)
        self._validate_range(area, address, count)
        return [
            self._encode_cell(self._point_at(area, address + offset))
            for offset in range(count)
        ]

    def write(
        self, area: RegisterArea | str, address: int, value: int | bool
    ) -> RegisterWrite:
        plan = self.preview_write(area, address, value)
        previous_value = self.read(area, address)[0]
        self.backend.write(plan.variable_id, plan.engineering_value)
        resulting_value = self.read(area, address)[0]
        return RegisterWrite(
            area=plan.area,
            address=address,
            previous_value=previous_value,
            requested_value=plan.requested_value,
            resulting_value=resulting_value,
            world_revision=self.backend.snapshot().revision,
        )

    def write_many(
        self, area: RegisterArea | str, address: int, values: Iterable[int | bool]
    ) -> list[RegisterWrite]:
        plans = self.preview_write_many(area, address, values)
        writes: list[RegisterWrite] = []
        for plan in plans:
            previous_value = self.read(plan.area, plan.address)[0]
            self.backend.write(plan.variable_id, plan.engineering_value)
            resulting_value = self.read(plan.area, plan.address)[0]
            writes.append(
                RegisterWrite(
                    area=plan.area,
                    address=plan.address,
                    previous_value=previous_value,
                    requested_value=plan.requested_value,
                    resulting_value=resulting_value,
                    world_revision=self.backend.snapshot().revision,
                )
            )
        return writes

    def preview_write(
        self, area: RegisterArea | str, address: int, value: int | bool
    ) -> ProcessRegisterWritePlan:
        """Decode one protocol write without mutating the process backend."""

        area = RegisterArea(area)
        self._validate_range(area, address, 1)
        if area not in {RegisterArea.COILS, RegisterArea.HOLDING_REGISTERS}:
            raise ProcessRegisterReadOnlyError(f"{area.value} is read-only")
        point = self._point_at(area, address)
        if "write" not in point.access:
            raise ProcessRegisterReadOnlyError(f"{point.variable_id} is read-only")

        requested_value = self._normalize_cell(area, value)
        engineering_value = self._decode_cell(point, requested_value)
        self._validate_engineering_value(point, engineering_value)
        return ProcessRegisterWritePlan(
            area=area,
            address=address,
            variable_id=point.variable_id,
            requested_value=requested_value,
            engineering_value=engineering_value,
        )

    def preview_write_many(
        self, area: RegisterArea | str, address: int, values: Iterable[int | bool]
    ) -> list[ProcessRegisterWritePlan]:
        """Decode multiple protocol writes without mutating the process backend."""

        area = RegisterArea(area)
        values = tuple(values)
        if not values:
            raise ProcessRegisterWriteError("process register write requires values")
        return [
            self.preview_write(area, address + offset, value)
            for offset, value in enumerate(values)
        ]

    def intent_for_write(self, area: RegisterArea | str, address: int) -> Intent:
        area = RegisterArea(area)
        point = self._point_at(area, address)
        variable = self.backend.variables[point.variable_id]
        if variable.role == "setpoint":
            return Intent.WRITE_SETPOINT
        if variable.role == "manipulated_variable":
            return Intent.CONTROL_OUTPUT
        return Intent.UNSUPPORTED_OPERATION

    def encode_blocks(self) -> dict[RegisterArea, list[int]]:
        blocks: dict[RegisterArea, list[int]] = {}
        for area in RegisterArea:
            size = self.block_size(area)
            blocks[area] = [self._encode_cell(self._point_at(area, address)) for address in range(size)]
        return blocks

    def _index_points(
        self, points: tuple[ProtocolPointMapping, ...]
    ) -> dict[RegisterArea, dict[int, ProtocolPointMapping]]:
        indexed: dict[RegisterArea, dict[int, ProtocolPointMapping]] = {}
        for point in points:
            area = RegisterArea(point.table)
            area_points = indexed.setdefault(area, {})
            if point.address in area_points:
                raise RegisterAccessError(
                    f"duplicate {area.value} address {point.address}"
                )
            area_points[point.address] = point
        return indexed

    def _validate_backend_variables(self) -> None:
        available = set(self.backend.variables)
        mapped = {
            point.variable_id
            for area_points in self._points.values()
            for point in area_points.values()
        }
        missing = mapped - available
        if missing:
            raise RegisterAccessError(
                f"scenario maps variables missing from backend: {sorted(missing)}"
            )

    def _validate_range(
        self, area: RegisterArea, address: int, count: int
    ) -> None:
        if address < 0:
            raise RegisterAccessError("register address must be non-negative")
        if count <= 0:
            raise RegisterAccessError("register count must be positive")
        for offset in range(count):
            self._point_at(area, address + offset)

    def _point_at(self, area: RegisterArea, address: int) -> ProtocolPointMapping:
        try:
            return self._points[area][address]
        except KeyError as exc:
            raise RegisterAccessError(
                f"{area.value} address {address} is not mapped"
            ) from exc

    def _encode_cell(self, point: ProtocolPointMapping) -> int:
        value = self.backend.read(point.variable_id)
        if point.data_type == "bool":
            return int(bool(value))
        raw_value = int(round(value * point.scale))
        if point.data_type == "uint16":
            return min(65535, max(0, raw_value))
        raise RegisterAccessError(f"unsupported data_type: {point.data_type}")

    def _decode_cell(self, point: ProtocolPointMapping, raw_value: int) -> float:
        if point.data_type == "bool":
            return float(bool(raw_value))
        if point.scale == 0:
            raise RegisterAccessError(f"{point.variable_id} has zero scale")
        return float(raw_value) / point.scale

    def _validate_engineering_value(
        self,
        point: ProtocolPointMapping,
        engineering_value: float,
    ) -> None:
        variable = self.backend.variables[point.variable_id]
        if variable.minimum is not None and engineering_value < variable.minimum:
            raise ProcessRegisterWriteError(
                f"{point.variable_id} must be >= {variable.minimum}"
            )
        if variable.maximum is not None and engineering_value > variable.maximum:
            raise ProcessRegisterWriteError(
                f"{point.variable_id} must be <= {variable.maximum}"
            )

    def _normalize_cell(self, area: RegisterArea, value: int | bool) -> int:
        if area is RegisterArea.COILS:
            return int(bool(value))
        normalized = int(value)
        if not 0 <= normalized <= 65535:
            raise RegisterAccessError("register value must fit in uint16")
        return normalized
