from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_plc.contracts.actions import WorldPatch, WorldPatchOperation
from agentic_plc.world.model import OperatingMode, TankPumpWorld


class WorldPatchError(ValueError):
    """Raised when a proposed world mutation is outside the allowed surface."""


@dataclass(frozen=True, slots=True)
class AppliedWorldPatch:
    patch: WorldPatch
    before: dict[str, object]
    after: dict[str, object]


class WorldPatchApplier:
    """Validates and applies bounded agent-generated mutations."""

    ALLOWED_PATHS = frozenset(
        {
            "level_percent",
            "pressure_bar",
            "level_setpoint_percent",
            "inlet_valve_open",
            "outlet_pump_running",
            "high_level_alarm",
            "mode",
        }
    )

    def validate(self, patch: WorldPatch) -> None:
        if not patch.actor_id.strip():
            raise WorldPatchError("world patch actor_id is required")
        if not patch.reason.strip():
            raise WorldPatchError("world patch reason is required")
        if not 1 <= patch.ttl_seconds <= 3600:
            raise WorldPatchError("world patch ttl_seconds must be between 1 and 3600")
        if not patch.operations:
            raise WorldPatchError("world patch requires at least one operation")
        if len(patch.operations) > 8:
            raise WorldPatchError("world patch supports at most 8 operations")

        for operation in patch.operations:
            self._validate_operation(operation)

    def apply(self, world: TankPumpWorld, patch: WorldPatch) -> AppliedWorldPatch:
        self.validate(patch)
        before = world.snapshot()
        for operation in patch.operations:
            self._apply_operation(world, operation)
        world.state.revision += 1
        return AppliedWorldPatch(patch=patch, before=before, after=world.snapshot())

    def _validate_operation(self, operation: WorldPatchOperation) -> None:
        if operation.path not in self.ALLOWED_PATHS:
            raise WorldPatchError(f"world patch path is not allowed: {operation.path}")
        self._coerce_value(operation.path, operation.value)

    def _apply_operation(
        self, world: TankPumpWorld, operation: WorldPatchOperation
    ) -> None:
        value = self._coerce_value(operation.path, operation.value)
        setattr(world.state, operation.path, value)

    def _coerce_value(self, path: str, value: Any) -> object:
        if path == "level_percent":
            return self._bounded_float(path, value, 0.0, 100.0)
        if path == "pressure_bar":
            return self._bounded_float(path, value, 0.0, 16.0)
        if path == "level_setpoint_percent":
            return self._bounded_float(path, value, 10.0, 90.0)
        if path in {"inlet_valve_open", "outlet_pump_running", "high_level_alarm"}:
            if isinstance(value, bool):
                return value
            if value in {0, 1}:
                return bool(value)
            raise WorldPatchError(f"{path} must be boolean")
        if path == "mode":
            try:
                return OperatingMode(str(value))
            except ValueError as exc:
                raise WorldPatchError(f"unsupported operating mode: {value}") from exc
        raise WorldPatchError(f"world patch path is not allowed: {path}")

    def _bounded_float(
        self,
        path: str,
        value: Any,
        minimum: float,
        maximum: float,
    ) -> float:
        number = float(value)
        if not minimum <= number <= maximum:
            raise WorldPatchError(f"{path} must be between {minimum} and {maximum}")
        return number
