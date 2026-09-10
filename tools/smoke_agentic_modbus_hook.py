from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path


def prepare_windows_conpot_vfs(repo_root: Path) -> None:
    os.chdir(repo_root)
    (repo_root / "tests" / "data" / "data_temp_fs").mkdir(
        parents=True, exist_ok=True
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke Conpot Modbus TCP with the agentic databank hook."
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use the OpenAI-compatible planner configured in .env.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=Path("records") / "agentic_modbus_hook_events.jsonl",
    )
    parser.add_argument(
        "--model",
        help="Override the .env model for --live-llm without printing secrets.",
    )
    args = parser.parse_args()

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
        ModbusHookContext,
        get_shared_tank_pump_runtime,
        install_agentic_modbus_hook,
        reset_shared_tank_pump_runtime,
    )
    from agentic_plc.agent import (
        AgentRuntime,
        LLMConfig,
        OpenAICompatiblePlanner,
        RuleBasedDeceptionPlanner,
    )
    from agentic_plc.telemetry import JsonlEventStore

    reset_shared_tank_pump_runtime("tank_pump_v1")
    template_dir = repo_root / "integrations" / "conpot" / "tank_pump"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))
    shared = get_shared_tank_pump_runtime("tank_pump_v1")

    config = LLMConfig.from_env(".env")
    if args.model:
        config = replace(config, model=args.model)
    if args.live_llm:
        planner = OpenAICompatiblePlanner(config)
        planner_name = "llm"
    else:
        planner = RuleBasedDeceptionPlanner()
        planner_name = "rule"

    store = JsonlEventStore(args.events)
    store.clear()
    runtime = AgentRuntime(
        world=shared.world,
        planner=planner,
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
            session_id="agentic-hook-smoke",
            source_ip="127.0.0.1",
            actor_id="actor-local-smoke",
        ),
    )
    greenlet = gevent.spawn(server.start, "127.0.0.1", 0)
    gevent.sleep(0.2)

    try:
        host = server.server.server_host
        port = server.server.server_port
        slave_id = 1

        master = modbus_tcp.TcpMaster(host=host, port=port)
        master.set_timeout(2.0)

        generated_holding = master.execute(
            slave=slave_id,
            function_code=cst.READ_HOLDING_REGISTERS,
            starting_address=0,
            quantity_of_x=2,
        )
        deterministic_inputs = master.execute(
            slave=slave_id,
            function_code=cst.READ_INPUT_REGISTERS,
            starting_address=0,
            quantity_of_x=2,
        )

        print(f"modbus endpoint: {host}:{port}")
        print(f"planner: {planner_name}")
        print(f"llm_configured: {config.is_configured}")
        print(f"generated holding registers: {generated_holding}")
        print(f"deterministic input registers: {deterministic_inputs}")
        print(f"agentic decisions: {len(runtime.decisions)}")
        print(f"events: {len(store.list_events())}")
        print(f"last agent error: {hook.last_agent_error}")
        return 0
    finally:
        server.stop()
        greenlet.kill()


if __name__ == "__main__":
    raise SystemExit(main())
