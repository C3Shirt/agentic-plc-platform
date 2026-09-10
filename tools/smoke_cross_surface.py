from __future__ import annotations

import json
import os
import urllib.request
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

    from agentic_plc.adapters import (
        create_tank_pump_hmi_server,
        get_shared_tank_pump_runtime,
        reset_shared_tank_pump_runtime,
    )

    reset_shared_tank_pump_runtime("tank_pump_v1")
    template_dir = repo_root / "integrations" / "conpot" / "tank_pump"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))
    runtime = get_shared_tank_pump_runtime("tank_pump_v1")

    modbus_server = ModbusServer(
        template=str(template_dir / "modbus" / "modbus.xml"),
        template_directory=str(template_dir),
        args=None,
    )
    modbus_greenlet = gevent.spawn(modbus_server.start, "127.0.0.1", 0)
    hmi_server = create_tank_pump_hmi_server(
        "127.0.0.1",
        0,
        world=runtime.world,
        event_sink=runtime.event_log,
    )
    hmi_greenlet = gevent.spawn(hmi_server.serve_forever)
    gevent.sleep(0.2)
    master = None

    try:
        modbus_host = modbus_server.server.server_host
        modbus_port = modbus_server.server.server_port
        hmi_host, hmi_port = hmi_server.server_address
        hmi_base_url = f"http://{hmi_host}:{hmi_port}"

        master = modbus_tcp.TcpMaster(host=modbus_host, port=modbus_port)
        master.set_timeout(2.0)
        master.execute(
            slave=1,
            function_code=cst.WRITE_SINGLE_REGISTER,
            starting_address=0,
            output_value=700,
        )
        hmi_after_modbus = get_json(f"{hmi_base_url}/api/state")["state"]

        post_json(
            f"{hmi_base_url}/api/control",
            {"action": "set_level_setpoint", "value": 72},
        )
        holding_after_hmi = master.execute(
            slave=1,
            function_code=cst.READ_HOLDING_REGISTERS,
            starting_address=0,
            quantity_of_x=2,
        )

        print(f"modbus endpoint: {modbus_host}:{modbus_port}")
        print(f"hmi endpoint: {hmi_base_url}")
        print(f"hmi setpoint after modbus write: {hmi_after_modbus['level_setpoint_percent']}")
        print(f"modbus holding after hmi write: {holding_after_hmi}")
        print(f"world revision: {runtime.world.state.revision}")
        print(f"events: {len(runtime.event_log.list_events())}")
        return 0
    finally:
        if master is not None:
            master.close()
        hmi_greenlet.kill()
        hmi_server.server_close()
        modbus_server.stop()
        modbus_greenlet.kill()


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
