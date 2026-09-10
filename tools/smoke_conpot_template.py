from __future__ import annotations

import os
from pathlib import Path

from agentic_plc.adapters.conpot_databus import get_shared_tank_pump_runtime


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    (repo_root / "tests" / "data" / "data_temp_fs").mkdir(
        parents=True, exist_ok=True
    )

    try:
        import conpot.core as conpot_core
        from conpot.protocols.modbus.modbus_server import ModbusServer
    except ModuleNotFoundError as exc:
        print(f"missing Conpot runtime dependency: {exc.name}")
        return 2

    template_dir = repo_root / "integrations" / "conpot" / "tank_pump"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))
    ModbusServer(
        template=str(template_dir / "modbus" / "modbus.xml"),
        template_directory=str(template_dir),
        args=None,
    )

    databus = conpot_core.get_databus()
    holding = databus.get_value("agenticPlcSlave1HoldingRegisters")
    coils = databus.get_value("agenticPlcSlave1Coils")

    print(f"holding before: {holding[0:2]}")
    holding[1] = 1
    coils[0] = 1
    print(f"holding after: {holding[0:2]}")
    print(f"coils after: {coils[0:2]}")

    runtime = get_shared_tank_pump_runtime("tank_pump_v1")
    print(f"world revision: {runtime.world.state.revision}")
    print(f"events: {len(runtime.event_log.list_events())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
