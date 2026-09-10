from __future__ import annotations

import os
from pathlib import Path


def prepare_windows_conpot_vfs(repo_root: Path) -> None:
    os.chdir(repo_root)
    (repo_root / "tests" / "data" / "data_temp_fs").mkdir(
        parents=True, exist_ok=True
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    prepare_windows_conpot_vfs(repo_root)
    asset_root = repo_root / "external" / "tennessee_eastman"
    if not (asset_root / "extracted" / "idv1" / "y.dat").exists():
        print("missing TE assets; run: python tools\\download_tennessee_eastman.py")
        return 2

    from gevent import monkey

    monkey.patch_all()

    import gevent
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    import conpot.core as conpot_core
    from conpot.protocols.modbus.modbus_server import ModbusServer

    from agentic_plc.adapters.conpot_databus import (
        get_shared_tennessee_eastman_runtime,
    )

    template_dir = repo_root / "integrations" / "conpot" / "tennessee_eastman"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))
    server = ModbusServer(
        template=str(template_dir / "modbus" / "modbus.xml"),
        template_directory=str(template_dir),
        args=None,
    )
    greenlet = gevent.spawn(server.start, "127.0.0.1", 0)
    gevent.sleep(0.2)

    try:
        host = server.server.server_host
        port = server.server.server_port
        slave_id = 1

        runtime = get_shared_tennessee_eastman_runtime("tennessee_eastman_idv1")
        runtime.backend.tick(600.0)

        master = modbus_tcp.TcpMaster(host=host, port=port)
        master.set_timeout(2.0)

        inputs = master.execute(
            slave=slave_id,
            function_code=cst.READ_INPUT_REGISTERS,
            starting_address=0,
            quantity_of_x=9,
        )
        holding_before = master.execute(
            slave=slave_id,
            function_code=cst.READ_HOLDING_REGISTERS,
            starting_address=0,
            quantity_of_x=8,
        )
        master.execute(
            slave=slave_id,
            function_code=cst.WRITE_SINGLE_REGISTER,
            starting_address=5,
            output_value=420,
        )
        holding_after = master.execute(
            slave=slave_id,
            function_code=cst.READ_HOLDING_REGISTERS,
            starting_address=0,
            quantity_of_x=8,
        )

        print(f"modbus endpoint: {host}:{port}")
        print(f"input registers: {inputs}")
        print(f"holding before: {holding_before}")
        print(f"holding after: {holding_after}")
        print(f"xmv_10 after write: {runtime.backend.read('xmv_10')}")
        print(f"backend revision: {runtime.backend.snapshot().revision}")
        print(f"events: {len(runtime.event_log.list_events())}")
        return 0
    finally:
        server.stop()
        greenlet.kill()


if __name__ == "__main__":
    raise SystemExit(main())
