from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class OperatingMode(StrEnum):
    STOP = "stop"
    MANUAL = "manual"
    AUTO = "auto"
    FAULT = "fault"


class ControlAction(StrEnum):
    SET_MODE = "set_mode"
    SET_LEVEL_SETPOINT = "set_level_setpoint"
    SET_INLET_VALVE = "set_inlet_valve"
    SET_OUTLET_PUMP = "set_outlet_pump"
    RESET_ALARM = "reset_alarm"


@dataclass(slots=True)
class TankPumpState:
    level_percent: float = 50.0
    pressure_bar: float = 1.2
    level_setpoint_percent: float = 65.0
    inlet_valve_open: bool = False
    outlet_pump_running: bool = False
    high_level_alarm: bool = False
    mode: OperatingMode = OperatingMode.AUTO
    revision: int = 0
    simulated_seconds: float = 0.0


class TankPumpWorld:
    """Small deterministic process model used as the first integration target."""

    HIGH_LEVEL_TRIP = 92.0
    HIGH_LEVEL_RESET = 85.0

    def __init__(self, state: TankPumpState | None = None) -> None:
        self.state = state or TankPumpState()

    def apply_control(self, action: ControlAction, value: object = None) -> None:
        if action is ControlAction.SET_MODE:
            self.state.mode = OperatingMode(str(value))
        elif action is ControlAction.SET_LEVEL_SETPOINT:
            setpoint = float(value)
            if not 10.0 <= setpoint <= 90.0:
                raise ValueError("level setpoint must be between 10 and 90 percent")
            self.state.level_setpoint_percent = setpoint
        elif action is ControlAction.SET_INLET_VALVE:
            self._require_manual_mode(action)
            self.state.inlet_valve_open = bool(value)
        elif action is ControlAction.SET_OUTLET_PUMP:
            self._require_manual_mode(action)
            self.state.outlet_pump_running = bool(value)
        elif action is ControlAction.RESET_ALARM:
            if self.state.level_percent >= self.HIGH_LEVEL_RESET:
                raise ValueError("high-level alarm cannot reset above reset threshold")
            self.state.high_level_alarm = False
        else:
            raise ValueError(f"unsupported control action: {action}")
        self.state.revision += 1

    def tick(self, seconds: float = 1.0) -> None:
        if seconds <= 0:
            raise ValueError("seconds must be positive")

        state = self.state
        if state.mode is OperatingMode.AUTO and not state.high_level_alarm:
            state.inlet_valve_open = state.level_percent < state.level_setpoint_percent
            state.outlet_pump_running = state.level_percent > (
                state.level_setpoint_percent + 5.0
            )
        elif state.mode in {OperatingMode.STOP, OperatingMode.FAULT}:
            state.inlet_valve_open = False
            state.outlet_pump_running = False

        inlet_rate = 0.32 if state.inlet_valve_open else 0.0
        outlet_rate = 0.45 if state.outlet_pump_running else 0.0
        state.level_percent = min(
            100.0,
            max(0.0, state.level_percent + (inlet_rate - outlet_rate) * seconds),
        )
        state.pressure_bar = 2.4 if state.outlet_pump_running else 1.2

        if state.level_percent >= self.HIGH_LEVEL_TRIP:
            state.high_level_alarm = True
            state.inlet_valve_open = False

        state.simulated_seconds += seconds
        state.revision += 1

    def snapshot(self) -> dict[str, object]:
        snapshot = asdict(self.state)
        snapshot["mode"] = self.state.mode.value
        return snapshot

    def _require_manual_mode(self, action: ControlAction) -> None:
        if self.state.mode is not OperatingMode.MANUAL:
            raise ValueError(f"{action.value} requires manual mode")

