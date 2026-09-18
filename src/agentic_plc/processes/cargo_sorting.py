from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .base import (
    ProcessSnapshot,
    ProcessVariable,
    ReadOnlyProcessVariable,
    UnknownProcessVariable,
)


def cargo_sorting_variables() -> tuple[ProcessVariable, ...]:
    """Canonical variables for the height-based cargo sorting cell.

    The addresses intentionally mirror the Factory I/O scene used during the
    lab exploration, but this backend is headless and deterministic. It can be
    driven directly by a Modbus-facing register map or used as a reference
    source for acquisition/learning tests.
    """

    bool_bounds = {"minimum": 0.0, "maximum": 1.0}
    return (
        ProcessVariable(
            "feeder_conveyor",
            "Feeder conveyor",
            "manipulated_variable",
            writable=True,
            description="Feeds boxes from the source into the cell.",
            **bool_bounds,
        ),
        ProcessVariable(
            "entry_conveyor",
            "Entry conveyor",
            "manipulated_variable",
            writable=True,
            description="Moves boxes toward the height classifier.",
            **bool_bounds,
        ),
        ProcessVariable(
            "load",
            "Load actuator",
            "manipulated_variable",
            writable=True,
            description="Pulse-style loader command used by some PLC programs.",
            **bool_bounds,
        ),
        ProcessVariable(
            "unload",
            "Unload actuator",
            "manipulated_variable",
            writable=True,
            description="Pulse-style unload command used by some PLC programs.",
            **bool_bounds,
        ),
        ProcessVariable(
            "turntable",
            "Turntable diverter",
            "manipulated_variable",
            writable=True,
            description="When energized, routes the current box to the right lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "left_conveyor",
            "Left conveyor",
            "manipulated_variable",
            writable=True,
            description="Moves boxes through the left/low-box lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "right_conveyor",
            "Right conveyor",
            "manipulated_variable",
            writable=True,
            description="Moves boxes through the right/high-box lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_load_position",
            "At load position",
            "measurement",
            description="A box is present at the loader.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_unload_position",
            "At unload position",
            "measurement",
            description="A box is present at an exit/unload position.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_front",
            "At front",
            "measurement",
            description="A box is at the front side of the turntable.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_back",
            "At back",
            "measurement",
            description="A box is at the back side of the turntable.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_entry",
            "At entry",
            "measurement",
            description="A box is detected at the entry conveyor.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_turntable_entry",
            "At turntable entry",
            "measurement",
            description="A box is approaching or occupying the turntable entry.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_right_entry",
            "At right entry",
            "measurement",
            description="A box is entering the right lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_left_entry",
            "At left entry",
            "measurement",
            description="A box is entering the left lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_left_exit",
            "At left exit",
            "measurement",
            description="A box is leaving the left lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "at_right_exit",
            "At right exit",
            "measurement",
            description="A box is leaving the right lane.",
            **bool_bounds,
        ),
        ProcessVariable(
            "high_box",
            "High box detected",
            "measurement",
            description="The classifier currently observes a high box.",
            **bool_bounds,
        ),
        ProcessVariable(
            "low_box",
            "Low box detected",
            "measurement",
            description="The classifier currently observes a low box.",
            **bool_bounds,
        ),
        ProcessVariable(
            "box_present",
            "Box present",
            "measurement",
            description="A synthetic box is currently in the cell.",
            **bool_bounds,
        ),
        ProcessVariable(
            "box_position",
            "Box position",
            "measurement",
            unit="pct",
            minimum=0.0,
            maximum=100.0,
            description="Normalized position along the sorting path.",
        ),
        ProcessVariable(
            "box_height_class",
            "Box height class",
            "measurement",
            minimum=0.0,
            maximum=2.0,
            description="0=no box, 1=low box, 2=high box.",
        ),
        ProcessVariable(
            "route_code",
            "Selected route",
            "measurement",
            minimum=0.0,
            maximum=2.0,
            description="0=not selected, 1=left lane, 2=right lane.",
        ),
        ProcessVariable(
            "sorted_left_count",
            "Sorted left count",
            "measurement",
            minimum=0.0,
            maximum=65535.0,
            description="Number of boxes completed through the left lane.",
        ),
        ProcessVariable(
            "sorted_right_count",
            "Sorted right count",
            "measurement",
            minimum=0.0,
            maximum=65535.0,
            description="Number of boxes completed through the right lane.",
        ),
    )


class CargoSortingProcessBackend:
    """Headless process backend for the height-based cargo sorting example.

    The backend deliberately models only attacker-observable consistency rather
    than high-fidelity mechanics. A box alternates high/low on each spawn. Low
    boxes should be routed left, and high boxes should be routed right when the
    turntable diverter is energized near the classifier. The resulting sensor
    timeline is stable enough to acquire traces, learn a replayable process
    model, and benchmark protocol/physical-state consistency without requiring
    Factory I/O.
    """

    process_id = "cargo_sorting_height_process"
    name = "cargo_sorting_headless"

    def __init__(
        self,
        *,
        process_id: str | None = None,
        name: str | None = None,
        spawn_high_first: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.process_id = process_id or self.process_id
        self.name = name or self.name
        self._variables = {
            variable.variable_id: variable for variable in cargo_sorting_variables()
        }
        self._spawn_high_first = bool(spawn_high_first)
        self._metadata = dict(metadata or {})
        self._revision = 0
        self._simulated_seconds = 0.0
        self._sequence_index = 0
        self._state = self._initial_state()
        self._sync_sensors()

    @property
    def variables(self) -> Mapping[str, ProcessVariable]:
        return dict(self._variables)

    def reset(self) -> None:
        self._revision = 0
        self._simulated_seconds = 0.0
        self._sequence_index = 0
        self._state = self._initial_state()
        self._sync_sensors()

    def snapshot(self) -> ProcessSnapshot:
        measurements: dict[str, float] = {}
        manipulated_variables: dict[str, float] = {}
        setpoints: dict[str, float] = {}

        for variable_id, variable in self._variables.items():
            value = self._state[variable_id]
            if variable.role == "measurement":
                measurements[variable_id] = value
            elif variable.role == "manipulated_variable":
                manipulated_variables[variable_id] = value
            elif variable.role == "setpoint":
                setpoints[variable_id] = value

        return ProcessSnapshot(
            process_id=self.process_id,
            backend_name=self.name,
            revision=self._revision,
            simulated_seconds=self._simulated_seconds,
            measurements=measurements,
            manipulated_variables=manipulated_variables,
            setpoints=setpoints,
            metadata={
                **self._metadata,
                "backend_type": "cargo_sorting",
                "sequence_index": self._sequence_index,
            },
        )

    def read(self, variable_id: str) -> float:
        self._require_variable(variable_id)
        return self._state[variable_id]

    def write(self, variable_id: str, value: float) -> None:
        variable = self._require_variable(variable_id)
        if not variable.writable:
            raise ReadOnlyProcessVariable(variable_id)
        self._state[variable_id] = self._bounded_value(variable, float(value))
        self._sync_sensors()
        self._revision += 1

    def write_internal(self, variable_id: str, value: float) -> None:
        variable = self._require_variable(variable_id)
        self._state[variable_id] = self._bounded_value(variable, float(value))
        self._sync_sensors()
        self._revision += 1

    def tick(self, seconds: float = 1.0) -> None:
        dt = float(seconds)
        if dt <= 0:
            raise ValueError("seconds must be positive")

        # Split long ticks so threshold crossings produce stable sensors.
        remaining = dt
        while remaining > 1.0e-9:
            step = min(0.25, remaining)
            self._advance(step)
            self._simulated_seconds += step
            remaining -= step
        self._revision += 1

    def _advance(self, dt: float) -> None:
        if not self._state["box_present"] and self._spawn_requested():
            self._spawn_box()

        if self._state["box_present"]:
            position = self._state["box_position"]
            speed = 20.0
            moving = False
            if position < 35.0:
                moving = bool(
                    self._state["feeder_conveyor"]
                    or self._state["entry_conveyor"]
                    or self._state["load"]
                )
            elif position < 55.0:
                moving = bool(self._state["entry_conveyor"])
            else:
                if self._state["route_code"] == 0.0:
                    self._state["route_code"] = (
                        2.0 if self._state["turntable"] else 1.0
                    )
                if self._state["route_code"] == 2.0:
                    moving = bool(self._state["right_conveyor"])
                else:
                    moving = bool(self._state["left_conveyor"])

            if moving:
                self._state["box_position"] = min(100.0, position + speed * dt)

            if self._state["box_position"] >= 99.5 or self._state["unload"]:
                if self._state["route_code"] == 2.0:
                    self._state["sorted_right_count"] += 1.0
                elif self._state["route_code"] == 1.0:
                    self._state["sorted_left_count"] += 1.0
                self._clear_box()

        self._sync_sensors()

    def _spawn_requested(self) -> bool:
        return bool(
            self._state["load"]
            or (self._state["feeder_conveyor"] and self._state["entry_conveyor"])
        )

    def _spawn_box(self) -> None:
        high_first = self._spawn_high_first
        high_box = (self._sequence_index % 2 == 0) if high_first else (
            self._sequence_index % 2 == 1
        )
        self._state["box_present"] = 1.0
        self._state["box_position"] = 0.0
        self._state["box_height_class"] = 2.0 if high_box else 1.0
        self._state["route_code"] = 0.0
        self._sequence_index += 1

    def _clear_box(self) -> None:
        self._state["box_present"] = 0.0
        self._state["box_position"] = 0.0
        self._state["box_height_class"] = 0.0
        self._state["route_code"] = 0.0

    def _sync_sensors(self) -> None:
        present = bool(self._state["box_present"])
        position = self._state["box_position"] if present else -1.0
        route = self._state["route_code"]
        height = self._state["box_height_class"]

        sensor_values = {
            "at_load_position": present and position <= 5.0,
            "at_entry": present and 8.0 <= position <= 20.0,
            "at_turntable_entry": present and 35.0 <= position <= 55.0,
            "at_front": present and 42.0 <= position <= 50.0,
            "at_back": present and 50.0 < position <= 58.0,
            "at_right_entry": present and route == 2.0 and 55.0 <= position <= 70.0,
            "at_left_entry": present and route == 1.0 and 55.0 <= position <= 70.0,
            "at_left_exit": present and route == 1.0 and position >= 88.0,
            "at_right_exit": present and route == 2.0 and position >= 88.0,
            "at_unload_position": present and position >= 88.0,
            "high_box": present and height == 2.0 and 28.0 <= position <= 52.0,
            "low_box": present and height == 1.0 and 28.0 <= position <= 52.0,
        }
        for variable_id, value in sensor_values.items():
            self._state[variable_id] = float(bool(value))

    def _initial_state(self) -> dict[str, float]:
        state = {variable_id: 0.0 for variable_id in self._variables}
        return state

    def _require_variable(self, variable_id: str) -> ProcessVariable:
        try:
            return self._variables[variable_id]
        except KeyError as exc:
            raise UnknownProcessVariable(variable_id) from exc

    def _bounded_value(self, variable: ProcessVariable, value: float) -> float:
        if variable.minimum is not None and value < variable.minimum:
            raise ValueError(f"{variable.variable_id} below minimum {variable.minimum}")
        if variable.maximum is not None and value > variable.maximum:
            raise ValueError(f"{variable.variable_id} above maximum {variable.maximum}")
        return float(value)


def cargo_sorting_initial_trace_backend_inputs(
    variables: Iterable[ProcessVariable] | None = None,
) -> dict[str, list[float]]:
    """Return a zero-valued trace series shape for artifact generation tests."""

    return {variable.variable_id: [0.0] for variable in variables or cargo_sorting_variables()}
