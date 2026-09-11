from __future__ import annotations

import json

from agentic_plc.adapters.conpot_agentic_modbus import (
    ModbusHookContext,
    event_from_modbus_tcp_request,
)
from agentic_plc.agent import (
    AgentController,
    PhysicalProcessContext,
    RuleBasedDeceptionPlanner,
)
from agentic_plc.processes import ProcessRegisterMap, ProcessVariable, ScenarioMapping
from agentic_plc.processes.trace import TraceProcessBackend
from agentic_plc.protocols.modbus import parse_modbus_tcp_frame


def main() -> int:
    process_context = _writable_process_context()
    controller = AgentController(
        RuleBasedDeceptionPlanner(),
        process_context=process_context,
    )

    write_event = event_from_modbus_tcp_request(
        _modbus_frame(18, 1, bytes.fromhex("06 00 00 02 bc")),
        context=ModbusHookContext(
            session_id="write-through-smoke",
            source_ip="192.0.2.10",
            actor_id="actor-write-through",
        ),
    )
    write_decision = controller.run_once([write_event])
    assert write_decision.rejected == []
    assert len(write_decision.world_patches) == 1
    assert len(write_decision.protocol_replies) == 1

    read_event = event_from_modbus_tcp_request(
        _modbus_frame(19, 1, bytes.fromhex("03 00 00 00 01")),
        context=ModbusHookContext(
            session_id="write-through-smoke",
            source_ip="192.0.2.10",
            actor_id="actor-write-through",
        ),
    )
    read_decision = controller.run_once([read_event])
    assert read_decision.rejected == []
    assert len(read_decision.protocol_replies) == 1

    write_frame = parse_modbus_tcp_frame(
        write_decision.protocol_replies[0].payload_hex
    )
    read_frame = parse_modbus_tcp_frame(read_decision.protocol_replies[0].payload_hex)

    print(
        json.dumps(
            {
                "process_id": process_context.process_id,
                "write_ack_hex": write_frame.raw.hex(),
                "write_patch": [
                    {
                        "path": operation.path,
                        "value": operation.value,
                    }
                    for operation in write_decision.world_patches[
                        0
                    ].patch.operations
                ],
                "readback_hex": read_frame.raw.hex(),
                "readback_register_value": int.from_bytes(read_frame.data[1:3], "big"),
                "backend_level_setpoint": process_context.backend.read("level_sp"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _writable_process_context() -> PhysicalProcessContext:
    backend = TraceProcessBackend(
        process_id="generic_writable_process",
        name="trace_with_overrides",
        time_seconds=[0.0],
        variables=[
            ProcessVariable(
                variable_id="level_sp",
                name="Level setpoint",
                role="setpoint",
                unit="%",
                minimum=0.0,
                maximum=100.0,
                writable=True,
            )
        ],
        series={"level_sp": (50.0,)},
    )
    scenario = ScenarioMapping.from_dict(
        {
            "scenario_id": "generic_writable_modbus_slice",
            "process_id": "generic_writable_process",
            "backend": {"type": "trace"},
            "plc_area": "synthetic_process_cell",
            "description": "Writable PLC slice for write-through smoke testing",
            "points": [
                {
                    "variable_id": "level_sp",
                    "protocol": "modbus",
                    "table": "holding_registers",
                    "address": 0,
                    "access": "read_write",
                    "data_type": "uint16",
                    "scale": 10.0,
                }
            ],
        }
    )
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=ProcessRegisterMap(backend, scenario),
    )


def _modbus_frame(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    raw = bytearray()
    raw.extend(transaction_id.to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend((1 + len(pdu)).to_bytes(2, "big"))
    raw.append(unit_id)
    raw.extend(pdu)
    return bytes(raw)


if __name__ == "__main__":
    raise SystemExit(main())
