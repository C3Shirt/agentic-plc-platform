from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.acquisition import (
    build_cargo_sorting_acquisition_plan,
    learn_process_model,
    parse_modbus_interaction,
    respond_with_register_map,
    write_modbus_trace_jsonl,
)
from agentic_plc.processes import CargoSortingProcessBackend, ProcessRegisterMap
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_read_request,
    build_modbus_tcp_write_single_coil_request,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a complete acquisition smoke artifact for the "
            "height-based cargo sorting example."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("records/cargo_sorting_acquisition"),
        help="Directory for generated acquisition artifacts.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only write artifacts; do not print the compact summary.",
    )
    args = parser.parse_args()

    plan = build_cargo_sorting_acquisition_plan()
    scenario = plan.scenario_mapping()
    backend = CargoSortingProcessBackend()
    registers = ProcessRegisterMap(backend, scenario)

    events = []
    timestamp = 0.0

    def interact(request_hex: str, *, tick_after: float = 0.0) -> None:
        nonlocal timestamp
        response_hex = respond_with_register_map(request_hex, registers)
        events.append(
            parse_modbus_interaction(
                request_hex,
                response_hex,
                timestamp=timestamp,
                metadata={"source": "cargo_sorting_smoke"},
            )
        )
        if tick_after > 0:
            backend.tick(tick_after)
            timestamp += tick_after

    tx = 1000

    def read(function_code: int, address: int, count: int) -> str:
        nonlocal tx
        tx += 1
        return build_modbus_tcp_read_request(
            transaction_id=tx,
            unit_id=1,
            function_code=function_code,
            address=address,
            count=count,
        )

    def write_coil(address: int, energized: bool) -> str:
        nonlocal tx
        tx += 1
        return build_modbus_tcp_write_single_coil_request(
            transaction_id=tx,
            unit_id=1,
            address=address,
            energized=energized,
        )

    # Observe the idle map, then energize conveyors and route the first high box.
    interact(read(1, 0, 7))
    interact(read(2, 0, 10))
    interact(write_coil(0, True))
    interact(write_coil(1, True))
    interact(write_coil(5, True))
    interact(write_coil(6, True), tick_after=1.0)
    interact(read(2, 0, 10), tick_after=1.0)
    interact(read(2, 24, 1))
    interact(read(2, 26, 1))
    interact(write_coil(4, True), tick_after=1.0)
    interact(read(4, 0, 5), tick_after=1.0)
    interact(read(2, 0, 10), tick_after=2.0)
    interact(read(4, 0, 5), tick_after=1.0)
    interact(read(2, 0, 10))
    interact(read(4, 0, 5))

    model = learn_process_model(
        scenario,
        events,
        variables=plan.process_variables(),
    )

    artifact_paths = plan.write_artifacts(args.output_dir)
    trace_path = args.output_dir / "interaction_trace.jsonl"
    model_path = args.output_dir / "learned_process_model.json"
    write_modbus_trace_jsonl(trace_path, events)
    model_path.write_text(
        json.dumps(model.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifact_paths["interaction_trace"] = trace_path
    artifact_paths["learned_process_model"] = model_path

    if not args.quiet:
        print(
            json.dumps(
                {
                    "scenario_id": scenario.scenario_id,
                    "event_count": len(events),
                    "mapped_variable_count": model.metadata["mapped_variable_count"],
                    "response_example_count": len(model.response_examples),
                    "write_effect_count": len(model.write_effects),
                    "artifacts": {
                        key: str(path) for key, path in artifact_paths.items()
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
