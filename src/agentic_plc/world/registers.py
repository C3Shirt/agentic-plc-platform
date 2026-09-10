from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from agentic_plc.world.model import ControlAction, OperatingMode, TankPumpWorld


class RegisterArea(StrEnum):
    COILS = "coils"
    DISCRETE_INPUTS = "discrete_inputs"
    INPUT_REGISTERS = "input_registers"
    HOLDING_REGISTERS = "holding_registers"


class RegisterAccessError(ValueError):
    """Raised when a register operation is outside the exposed PLC surface."""


@dataclass(frozen=True, slots=True)
class RegisterWrite:
    area: RegisterArea
    address: int
    previous_value: int
    requested_value: int
    resulting_value: int
    world_revision: int


class TankPumpRegisterMap:
    """Encodes the tank-pump process as a small Modbus-style register map."""

    MODE_TO_CODE = {
        OperatingMode.STOP: 0,
        OperatingMode.MANUAL: 1,
        OperatingMode.AUTO: 2,
        OperatingMode.FAULT: 3,
    }
    CODE_TO_MODE = {code: mode for mode, code in MODE_TO_CODE.items()}

    def __init__(self, world: TankPumpWorld) -> None:
        self.world = world

    def block_size(self, area: RegisterArea | str) -> int:
        return len(self.encode_blocks()[RegisterArea(area)])

    def read(self, area: RegisterArea | str, address: int, count: int = 1) -> list[int]:
        area = RegisterArea(area)
        self._validate_range(area, address, count)
        block = self.encode_blocks()[area]
        return block[address : address + count]

    def write(
        self, area: RegisterArea | str, address: int, value: int | bool
    ) -> RegisterWrite:
        area = RegisterArea(area)
        self._validate_range(area, address, 1)

        if area not in {RegisterArea.COILS, RegisterArea.HOLDING_REGISTERS}:
            raise RegisterAccessError(f"{area.value} is read-only")

        requested_value = self._normalize_cell(area, value)
        previous_value = self.read(area, address)[0]

        if area is RegisterArea.COILS:
            self._write_coil(address, requested_value)
        else:
            self._write_holding_register(address, requested_value)

        resulting_value = self.read(area, address)[0]
        return RegisterWrite(
            area=area,
            address=address,
            previous_value=previous_value,
            requested_value=requested_value,
            resulting_value=resulting_value,
            world_revision=self.world.state.revision,
        )

    def write_many(
        self, area: RegisterArea | str, address: int, values: Iterable[int | bool]
    ) -> list[RegisterWrite]:
        writes: list[RegisterWrite] = []
        for offset, value in enumerate(values):
            writes.append(self.write(area, address + offset, value))
        return writes

    def encode_blocks(self) -> dict[RegisterArea, list[int]]:
        state = self.world.state
        return {
            RegisterArea.COILS: [
                int(state.outlet_pump_running),
                int(state.inlet_valve_open),
            ],
            RegisterArea.DISCRETE_INPUTS: [
                int(state.high_level_alarm),
            ],
            RegisterArea.INPUT_REGISTERS: [
                self._scale(state.level_percent, 10),
                self._scale(state.pressure_bar, 100),
            ],
            RegisterArea.HOLDING_REGISTERS: [
                self._scale(state.level_setpoint_percent, 10),
                self.MODE_TO_CODE[state.mode],
            ],
        }

    def _write_coil(self, address: int, value: int) -> None:
        if address == 0:
            self.world.apply_control(ControlAction.SET_OUTLET_PUMP, bool(value))
            return
        if address == 1:
            self.world.apply_control(ControlAction.SET_INLET_VALVE, bool(value))
            return
        raise RegisterAccessError(f"unsupported coil address: {address}")

    def _write_holding_register(self, address: int, value: int) -> None:
        if address == 0:
            self.world.apply_control(ControlAction.SET_LEVEL_SETPOINT, value / 10.0)
            return
        if address == 1:
            mode = self.CODE_TO_MODE.get(value)
            if mode is None:
                raise RegisterAccessError(f"unsupported operating mode code: {value}")
            self.world.apply_control(ControlAction.SET_MODE, mode.value)
            return
        raise RegisterAccessError(f"unsupported holding-register address: {address}")

    def _validate_range(
        self, area: RegisterArea, address: int, count: int
    ) -> None:
        if address < 0:
            raise RegisterAccessError("register address must be non-negative")
        if count <= 0:
            raise RegisterAccessError("register count must be positive")
        block = self.encode_blocks()[area]
        if address + count > len(block):
            raise RegisterAccessError(
                f"{area.value} range {address}:{address + count} is not mapped"
            )

    def _normalize_cell(self, area: RegisterArea, value: int | bool) -> int:
        if area is RegisterArea.COILS:
            return int(bool(value))
        normalized = int(value)
        if not 0 <= normalized <= 65535:
            raise RegisterAccessError("register value must fit in uint16")
        return normalized

    def _scale(self, value: float, factor: int) -> int:
        scaled = int(round(value * factor))
        return min(65535, max(0, scaled))
