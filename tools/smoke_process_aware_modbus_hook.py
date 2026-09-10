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

    import conpot.core as conpot_core
    import gevent
    import modbus_tk.defines as cst
    import modbus_tk.modbus_tcp as modbus_tcp
    from conpot.protocols.modbus.modbus_server import ModbusServer

    from agentic_plc.adapters.conpot_agentic_modbus import (
        ModbusHookContext,
        install_agentic_modbus_hook,
    )
    from agentic_plc.adapters.conpot_databus import (
        get_shared_tennessee_eastman_runtime,
    )
    from agentic_plc.agent import (
        AgentRuntime,
        PhysicalProcessContext,
        RuleBasedDeceptionPlanner,
    )
    from agentic_plc.telemetry import JsonlEventStore

    template_dir = repo_root / "integrations" / "conpot" / "tennessee_eastman"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))
    shared = get_shared_tennessee_eastman_runtime("tennessee_eastman_idv1")
    shared.backend.tick(600.0)
    process_context = PhysicalProcessContext(
        backend=shared.backend,
        scenario=shared.scenario,
        register_map=shared.register_map,
    )
    store = JsonlEventStore(Path("records") / "process_aware_modbus_hook.jsonl")
    store.clear()
    runtime = AgentRuntime(
        process_context=process_context,
        planner=RuleBasedDeceptionPlanner(),
        event_store=store,
    )

    server = ModbusServer(
        template=str(template_dir / "modbus" / "modbus.xml"),
        template_directory=str(template_dir),
        args=None,
    )
    hook = install_agentic_modbus_hook(
        server,
        runtime,
        context=ModbusHookContext(
            session_id="process-aware-hook-smoke",
            source_ip="127.0.0.1",
        ),
    )
    greenlet = gevent.spawn(server.start, "127.0.0.1", 0)
    gevent.sleep(0.2)

    try:
        master = modbus_tcp.TcpMaster(
            host=server.server.server_host,
            port=server.server.server_port,
        )
        master.set_timeout(2.0)
        inputs = master.execute(
            slave=1,
            function_code=cst.READ_INPUT_REGISTERS,
            starting_address=0,
            quantity_of_x=3,
        )
        latest = runtime.latest_decision()
        print(f"modbus endpoint: {server.server.server_host}:{server.server.server_port}")
        print(f"generated input registers: {inputs}")
        print(f"process id: {process_context.process_id}")
        print(f"backend: {process_context.backend_name}")
        print(
            "latest decision has generated reply: "
            f"{bool(latest and latest.protocol_replies)}"
        )
        print(f"hook error: {hook.last_agent_error}")
        print(f"events: {len(list(store.iter_events()))}")
        return 0
    finally:
        server.stop()
        greenlet.kill()


if __name__ == "__main__":
    raise SystemExit(main())
