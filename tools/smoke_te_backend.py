from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)
from agentic_plc.world.registers import RegisterArea


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test Tennessee Eastman trace backend and scenario mapping."
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=Path("external/tennessee_eastman"),
        help="directory populated by download_tennessee_eastman.py",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/tennessee_eastman/scenario.json"),
        help="scenario JSON mapping",
    )
    parser.add_argument("--idv", default="idv1")
    args = parser.parse_args()

    mapping = ScenarioMapping.from_file(args.scenario)
    backend = TennesseeEastmanTraceBackend.from_asset_root(args.asset_root, idv=args.idv)
    registers = ProcessRegisterMap(backend, mapping)
    before = backend.snapshot()
    backend.tick(600.0)
    after_tick = backend.snapshot()
    holding_before_write = registers.read(RegisterArea.HOLDING_REGISTERS, 5)[0]
    write = registers.write(RegisterArea.HOLDING_REGISTERS, 5, 420)
    after_write = backend.snapshot()

    exposed = {}
    for point in mapping.variables_for_protocol("modbus"):
        exposed[point.tag or point.variable_id] = {
            "variable_id": point.variable_id,
            "value": after_write.read(point.variable_id),
            "raw_register": round(after_write.read(point.variable_id) * point.scale),
            "table": point.table,
            "address": point.address,
        }

    output = {
        "backend": backend.name,
        "process_id": backend.process_id,
        "scenario_id": mapping.scenario_id,
        "row_count": after_write.metadata["row_count"],
        "time_before_seconds": before.simulated_seconds,
        "time_after_tick_seconds": after_tick.simulated_seconds,
        "time_after_write_seconds": after_write.simulated_seconds,
        "revision": after_write.revision,
        "measurement_count": len(after_write.measurements),
        "manipulated_variable_count": len(after_write.manipulated_variables),
        "setpoint_count": len(after_write.setpoints),
        "modbus_points": len(mapping.variables_for_protocol("modbus")),
        "input_registers_0_8": registers.read(RegisterArea.INPUT_REGISTERS, 0, 9),
        "holding_register_5_before_write": holding_before_write,
        "holding_register_5_after_write": write.resulting_value,
        "xmv_10_after_write": backend.read("xmv_10"),
        "sample_exposed_points": exposed,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
