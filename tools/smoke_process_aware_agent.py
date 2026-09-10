from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.agent import (
    AgentController,
    PhysicalProcessContext,
    RuleBasedDeceptionPlanner,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)
from agentic_plc.protocols.modbus import parse_modbus_tcp_frame


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the generic process-aware agent context."
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
    args = parser.parse_args()

    if not (args.asset_root / "extracted" / "idv1" / "y.dat").exists():
        print("missing TE assets; run: python tools\\download_tennessee_eastman.py")
        return 2

    scenario = ScenarioMapping.from_file(args.scenario)
    backend = TennesseeEastmanTraceBackend.from_asset_root(args.asset_root)
    backend.tick(600.0)
    register_map = ProcessRegisterMap(backend, scenario)
    context = PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )
    event = ICSEvent(
        protocol="modbus",
        session_id="process-aware-smoke",
        source_ip="127.0.0.1",
        actor_id="actor-local",
        intent=Intent.READ_PROCESS,
        operation="read_input_registers",
        transaction_id="17",
        unit_id=1,
        address=0,
        count=3,
        result="observed",
        metadata={"function_code": 4},
    )
    decision = AgentController(
        RuleBasedDeceptionPlanner(),
        process_context=context,
    ).run_once([event])
    if not decision.protocol_replies:
        print("no generated process-aware protocol reply")
        return 1

    reply = decision.protocol_replies[0]
    frame = parse_modbus_tcp_frame(reply.payload_hex)
    output = {
        "agent_context": {
            "backend_name": context.backend_name,
            "process_id": context.process_id,
            "scenario_id": scenario.scenario_id,
            "plc_area": scenario.plc_area,
            "writable_variable_ids": context.writable_variable_ids(),
        },
        "event": {
            "operation": event.operation,
            "address": event.address,
            "count": event.count,
            "function_code": event.metadata["function_code"],
        },
        "reply": {
            "transaction_id": frame.transaction_id,
            "unit_id": frame.unit_id,
            "function_code": frame.function_code,
            "payload_hex": reply.payload_hex,
            "register_values": context.values_for_modbus_event(event),
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
