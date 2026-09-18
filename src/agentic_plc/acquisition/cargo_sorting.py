from __future__ import annotations

from agentic_plc.processes.cargo_sorting import cargo_sorting_variables

from .planning import (
    PLCPointPlan,
    PLCProgramPlan,
    SceneComponentPlan,
    ScenarioAcquisitionPlan,
)


def build_cargo_sorting_acquisition_plan() -> ScenarioAcquisitionPlan:
    """Build the first concrete scene plan: height-based cargo sorting."""

    variable_by_id = {variable.variable_id: variable for variable in cargo_sorting_variables()}

    def point(
        variable_id: str,
        *,
        table: str,
        address: int,
        access: str,
        data_type: str = "bool",
        scale: float = 1.0,
        tag: str | None = None,
    ) -> PLCPointPlan:
        variable = variable_by_id[variable_id]
        return PLCPointPlan(
            variable_id=variable.variable_id,
            name=variable.name,
            role=variable.role,
            table=table,
            address=address,
            access=access,
            data_type=data_type,
            scale=scale,
            unit=variable.unit,
            minimum=variable.minimum,
            maximum=variable.maximum,
            writable=variable.writable,
            tag=tag,
            description=variable.description,
            metadata=dict(variable.metadata),
        )

    return ScenarioAcquisitionPlan(
        plan_id="cargo_sorting_height_acquisition_v1",
        scenario_id="cargo_sorting_height_v1",
        process_id="cargo_sorting_height_process",
        backend_type="cargo_sorting",
        plc_area="height_based_sorting_cell",
        description=(
            "A conveyor/turntable cell that classifies boxes by height and "
            "routes low boxes left and high boxes right through Modbus-visible "
            "actuator coils, sensor discrete inputs, and diagnostic input "
            "registers."
        ),
        components=(
            SceneComponentPlan(
                "source_feeder",
                "conveyor",
                "Feeds boxes into the sorting cell.",
                ("feeder_conveyor", "at_load_position", "box_present"),
            ),
            SceneComponentPlan(
                "entry_classifier",
                "conveyor_and_height_sensor",
                "Moves boxes to the classifier and exposes high/low sensors.",
                ("entry_conveyor", "at_entry", "high_box", "low_box"),
            ),
            SceneComponentPlan(
                "turntable_diverter",
                "diverter",
                "Selects the left or right route once a box reaches the table.",
                (
                    "turntable",
                    "at_turntable_entry",
                    "at_front",
                    "at_back",
                    "route_code",
                ),
            ),
            SceneComponentPlan(
                "left_lane",
                "conveyor",
                "Carries low boxes to the left exit.",
                ("left_conveyor", "at_left_entry", "at_left_exit", "sorted_left_count"),
            ),
            SceneComponentPlan(
                "right_lane",
                "conveyor",
                "Carries high boxes to the right exit.",
                (
                    "right_conveyor",
                    "at_right_entry",
                    "at_right_exit",
                    "sorted_right_count",
                ),
            ),
        ),
        points=(
            point("feeder_conveyor", table="coils", address=0, access="read_write", tag="Q0.0"),
            point("entry_conveyor", table="coils", address=1, access="read_write", tag="Q0.1"),
            point("load", table="coils", address=2, access="read_write", tag="Q0.2"),
            point("unload", table="coils", address=3, access="read_write", tag="Q0.3"),
            point("turntable", table="coils", address=4, access="read_write", tag="Q0.4"),
            point("left_conveyor", table="coils", address=5, access="read_write", tag="Q0.5"),
            point("right_conveyor", table="coils", address=6, access="read_write", tag="Q0.6"),
            point("at_load_position", table="discrete_inputs", address=0, access="read", tag="I0.0"),
            point("at_unload_position", table="discrete_inputs", address=1, access="read", tag="I0.1"),
            point("at_front", table="discrete_inputs", address=2, access="read", tag="I0.2"),
            point("at_back", table="discrete_inputs", address=3, access="read", tag="I0.3"),
            point("at_entry", table="discrete_inputs", address=4, access="read", tag="I0.4"),
            point("at_turntable_entry", table="discrete_inputs", address=5, access="read", tag="I0.5"),
            point("at_right_entry", table="discrete_inputs", address=6, access="read", tag="I0.6"),
            point("at_left_entry", table="discrete_inputs", address=7, access="read", tag="I0.7"),
            point("at_left_exit", table="discrete_inputs", address=8, access="read", tag="I1.0"),
            point("at_right_exit", table="discrete_inputs", address=9, access="read", tag="I1.1"),
            point("high_box", table="discrete_inputs", address=24, access="read", tag="I3.0"),
            point("low_box", table="discrete_inputs", address=26, access="read", tag="I3.2"),
            point("box_position", table="input_registers", address=0, access="read", data_type="uint16", scale=10.0, tag="IW0"),
            point("route_code", table="input_registers", address=1, access="read", data_type="uint16", tag="IW2"),
            point("sorted_left_count", table="input_registers", address=2, access="read", data_type="uint16", tag="IW4"),
            point("sorted_right_count", table="input_registers", address=3, access="read", data_type="uint16", tag="IW6"),
            point("box_height_class", table="input_registers", address=4, access="read", data_type="uint16", tag="IW8"),
        ),
        plc_states=(
            "IDLE",
            "FEEDING",
            "CLASSIFYING",
            "ROUTE_LEFT",
            "ROUTE_RIGHT",
            "UNLOADING",
            "FAULT",
        ),
        plc_program=PLCProgramPlan(
            name="cargo_sorting_height_controller",
            language="structured_text",
            code=_CARGO_SORTING_ST,
            notes=(
                "Skeleton only: the human can adapt I/O declarations and task "
                "cycle timing in CODESYS before acquisition."
            ),
        ),
        metadata={
            "process_family": "discrete_material_handling",
            "reference_scene": "height_based_sorting",
            "acquisition_stage": "planning",
            "route_policy": {
                "low_box": "left_lane",
                "high_box": "right_lane",
            },
        },
    )


_CARGO_SORTING_ST = """
TYPE E_SortState :
(
    IDLE,
    FEEDING,
    CLASSIFYING,
    ROUTE_LEFT,
    ROUTE_RIGHT,
    UNLOADING,
    FAULT
);
END_TYPE

VAR
    state : E_SortState := IDLE;
END_VAR

CASE state OF
    IDLE:
        feeder_conveyor := FALSE;
        entry_conveyor := FALSE;
        turntable := FALSE;
        left_conveyor := FALSE;
        right_conveyor := FALSE;
        IF start_cmd THEN
            state := FEEDING;
        END_IF;

    FEEDING:
        feeder_conveyor := TRUE;
        entry_conveyor := TRUE;
        left_conveyor := TRUE;
        right_conveyor := TRUE;
        IF at_turntable_entry THEN
            state := CLASSIFYING;
        END_IF;

    CLASSIFYING:
        IF high_box THEN
            turntable := TRUE;
            state := ROUTE_RIGHT;
        ELSIF low_box THEN
            turntable := FALSE;
            state := ROUTE_LEFT;
        END_IF;

    ROUTE_LEFT:
        left_conveyor := TRUE;
        right_conveyor := FALSE;
        IF at_left_exit THEN
            state := UNLOADING;
        END_IF;

    ROUTE_RIGHT:
        left_conveyor := FALSE;
        right_conveyor := TRUE;
        IF at_right_exit THEN
            state := UNLOADING;
        END_IF;

    UNLOADING:
        unload := TRUE;
        IF NOT at_unload_position THEN
            unload := FALSE;
            state := FEEDING;
        END_IF;

    FAULT:
        feeder_conveyor := FALSE;
        entry_conveyor := FALSE;
        turntable := FALSE;
        left_conveyor := FALSE;
        right_conveyor := FALSE;
END_CASE;
""".strip()
