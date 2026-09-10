from __future__ import annotations

import os
from pathlib import Path

from agentic_plc.adapters.conpot_databus import get_shared_tennessee_eastman_runtime


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    os.chdir(repo_root)
    (repo_root / "tests" / "data" / "data_temp_fs").mkdir(
        parents=True, exist_ok=True
    )
    asset_root = repo_root / "external" / "tennessee_eastman"
    if not (asset_root / "extracted" / "idv1" / "y.dat").exists():
        print("missing TE assets; run: python tools\\download_tennessee_eastman.py")
        return 2

    try:
        import conpot.core as conpot_core
        from conpot.protocols.modbus.modbus_server import ModbusServer
    except ModuleNotFoundError as exc:
        print(f"missing Conpot runtime dependency: {exc.name}")
        return 2

    template_dir = repo_root / "integrations" / "conpot" / "tennessee_eastman"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))
    ModbusServer(
        template=str(template_dir / "modbus" / "modbus.xml"),
        template_directory=str(template_dir),
        args=None,
    )

    runtime = get_shared_tennessee_eastman_runtime("tennessee_eastman_idv1")
    runtime.backend.tick(600.0)
    databus = conpot_core.get_databus()
    inputs = databus.get_value("agenticPlcSlave1InputRegisters")
    holding = databus.get_value("agenticPlcSlave1HoldingRegisters")

    print(f"input registers: {inputs[0:9]}")
    print(f"holding before: {holding[0:8]}")
    holding[5] = 420
    print(f"holding after: {holding[0:8]}")
    print(f"xmv_10 after write: {runtime.backend.read('xmv_10')}")
    print(f"backend revision: {runtime.backend.snapshot().revision}")
    print(f"events: {len(runtime.event_log.list_events())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
