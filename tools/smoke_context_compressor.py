from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.agent import ContextBudget, PhysicalProcessContext, ProcessContextCompressor
from agentic_plc.agent.config import LLMConfig
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test process-aware context compression."
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=Path("external/tennessee_eastman"),
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/tennessee_eastman/scenario.json"),
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=None,
        help="Override LLM_CONTEXT_MAX_POINTS for this smoke run.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Override LLM_CONTEXT_MAX_EVENTS for this smoke run.",
    )
    args = parser.parse_args()

    if not (args.asset_root / "extracted" / "idv1" / "y.dat").exists():
        print("missing TE assets; run: python tools\\download_tennessee_eastman.py")
        return 2

    config = LLMConfig.from_env(".env", environ={})
    max_points = args.max_points or config.context_max_points
    max_events = args.max_events or config.context_max_events

    scenario = ScenarioMapping.from_file(args.scenario)
    backend = TennesseeEastmanTraceBackend.from_asset_root(args.asset_root)
    backend.tick(600.0)
    register_map = ProcessRegisterMap(backend, scenario)
    context = PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )
    write_result = register_map.write("holding_registers", 5, 420)
    events = [
        ICSEvent(
            protocol="modbus",
            session_id="compressor-smoke",
            source_ip="127.0.0.1",
            actor_id="actor-local",
            intent=Intent.READ_PROCESS,
            operation="read_holding_registers",
            transaction_id="10",
            unit_id=1,
            address=5,
            count=1,
            result="observed",
            metadata={"function_code": 3},
        ),
        ICSEvent(
            protocol="modbus",
            session_id="compressor-smoke",
            source_ip="127.0.0.1",
            actor_id="actor-local",
            intent=Intent.CONTROL_OUTPUT,
            operation="write_single_register",
            transaction_id="11",
            unit_id=1,
            address=5,
            previous_value=write_result.previous_value,
            requested_value=420,
            resulting_value=write_result.resulting_value,
            result="observed",
            world_revision=write_result.world_revision,
            metadata={"function_code": 6},
        ),
        ICSEvent(
            protocol="modbus",
            session_id="compressor-smoke",
            source_ip="127.0.0.1",
            actor_id="actor-local",
            intent=Intent.READ_PROCESS,
            operation="read_input_registers",
            transaction_id="12",
            unit_id=1,
            address=0,
            count=3,
            result="observed",
            metadata={"function_code": 4},
        ),
    ]

    compressed = ProcessContextCompressor(
        ContextBudget(max_points=max_points, max_events=max_events)
    ).compress(context, events)
    payload = compressed.to_prompt_dict()
    output = {
        "process_card": payload["process_card"],
        "request_focus": payload["request_focus"],
        "selected_points": [
            {
                "variable_id": point["variable_id"],
                "tag": point["tag"],
                "table": point["table"],
                "address": point["address"],
                "encoded_value": point["encoded_value"],
                "reason_codes": point["reason_codes"],
            }
            for point in payload["exposed_points"]
        ],
        "writable_variable_ids": payload["writable_variable_ids"],
        "actor_memory": payload["actor_memory"],
        "compression": {
            key: value
            for key, value in payload["compression"].items()
            if key != "ranking"
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
