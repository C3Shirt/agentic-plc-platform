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

    from gevent import monkey

    monkey.patch_all()

    import gevent
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    import conpot.core as conpot_core
    from conpot.protocols.modbus.modbus_server import ModbusServer

    from agentic_plc.adapters.conpot_databus import get_shared_tank_pump_runtime

    template_dir = repo_root / "integrations" / "conpot" / "tank_pump"
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

        master = modbus_tcp.TcpMaster(host=host, port=port)
        master.set_timeout(2.0)

        holding_before = master.execute(
            slave=slave_id,
            function_code=cst.READ_HOLDING_REGISTERS,
            starting_address=0,
            quantity_of_x=2,
        )
        master.execute(
            slave=slave_id,
            function_code=cst.WRITE_SINGLE_REGISTER,
            starting_address=1,
            output_value=1,
        )
        master.execute(
            slave=slave_id,
            function_code=cst.WRITE_MULTIPLE_COILS,
            starting_address=0,
            output_value=[1, 1],
        )

        runtime = get_shared_tank_pump_runtime("tank_pump_v1")
        runtime.world.tick(1.0)

        coils_after = master.execute(
            slave=slave_id,
            function_code=cst.READ_COILS,
            starting_address=0,
            quantity_of_x=2,
        )
        inputs_after = master.execute(
            slave=slave_id,
            function_code=cst.READ_INPUT_REGISTERS,
            starting_address=0,
            quantity_of_x=2,
        )
        holding_after = master.execute(
            slave=slave_id,
            function_code=cst.READ_HOLDING_REGISTERS,
            starting_address=0,
            quantity_of_x=2,
        )

        print(f"modbus endpoint: {host}:{port}")
        print(f"holding before: {holding_before}")
        print(f"holding after: {holding_after}")
        print(f"coils after: {coils_after}")
        print(f"input registers after tick: {inputs_after}")
        print(f"world revision: {runtime.world.state.revision}")
        print(f"events: {len(runtime.event_log.list_events())}")
        return 0
    finally:
        server.stop()
        greenlet.kill()


if __name__ == "__main__":
    raise SystemExit(main())
