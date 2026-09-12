from __future__ import annotations

import argparse
import signal
from dataclasses import replace
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local agentic PLC honeypot with Modbus TCP and HTTP HMI "
            "surfaces backed by the same tank-pump world model."
        )
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--modbus-port", type=int, default=5020)
    parser.add_argument("--hmi-port", type=int, default=8080)
    parser.add_argument("--events", type=Path, default=Path("records") / "honeypot_events.jsonl")
    parser.add_argument(
        "--tick-seconds",
        type=float,
        default=1.0,
        help="Advance the simulated process by this many seconds per loop.",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use the OpenAI-compatible planner configured in .env.",
    )
    parser.add_argument(
        "--model",
        help="Override the .env model for --live-llm without printing secrets.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    _prepare_conpot_runtime_dirs(repo_root)

    from gevent import monkey

    monkey.patch_all()

    import gevent
    import conpot.core as conpot_core
    from conpot.protocols.modbus.modbus_server import ModbusServer

    from agentic_plc.adapters import (
        ModbusHookContext,
        get_shared_tank_pump_runtime,
        install_agentic_modbus_hook,
        reset_shared_tank_pump_runtime,
    )
    from agentic_plc.adapters.http_hmi import create_tank_pump_hmi_server
    from agentic_plc.agent import (
        AgentRuntime,
        LLMConfig,
        OpenAICompatiblePlanner,
        RuleBasedDeceptionPlanner,
    )
    from agentic_plc.telemetry import JsonlEventStore

    reset_shared_tank_pump_runtime("tank_pump_v1")
    shared = get_shared_tank_pump_runtime("tank_pump_v1")
    template_dir = repo_root / "integrations" / "conpot" / "tank_pump"
    conpot_core.get_databus().initialize(str(template_dir / "template.xml"))

    config = LLMConfig.from_env(".env")
    if args.model:
        config = replace(config, model=args.model)
    if args.live_llm:
        planner = OpenAICompatiblePlanner(config)
        planner_name = "llm"
    else:
        planner = RuleBasedDeceptionPlanner()
        planner_name = "rule"

    event_store = JsonlEventStore(args.events)
    runtime = AgentRuntime(
        world=shared.world,
        planner=planner,
        event_store=event_store,
    )

    modbus_server = ModbusServer(
        template=str(template_dir / "modbus" / "modbus.xml"),
        template_directory=str(template_dir),
        args=None,
    )
    install_agentic_modbus_hook(
        modbus_server,
        runtime,
        context=ModbusHookContext(
            session_id="agentic-honeypot-modbus",
            source_ip="0.0.0.0",
        ),
    )
    hmi_server = create_tank_pump_hmi_server(
        args.host,
        args.hmi_port,
        shared.world,
        event_sink=event_store,
    )

    stopping = {"value": False}

    def stop(_signum: int, _frame: object) -> None:
        stopping["value"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    modbus_greenlet = gevent.spawn(
        modbus_server.start,
        args.host,
        args.modbus_port,
    )
    hmi_greenlet = gevent.spawn(hmi_server.serve_forever)
    gevent.sleep(0.5)

    print(
        "agentic PLC honeypot started "
        f"modbus={args.host}:{args.modbus_port} "
        f"hmi=http://{args.host}:{args.hmi_port} "
        f"planner={planner_name} "
        f"llm_configured={config.is_configured} "
        f"events={args.events}",
        flush=True,
    )

    try:
        while not stopping["value"]:
            gevent.sleep(args.tick_seconds)
            shared.world.tick(args.tick_seconds)
            if modbus_greenlet.ready():
                modbus_greenlet.get()
            if hmi_greenlet.ready():
                hmi_greenlet.get()
    finally:
        hmi_server.shutdown()
        hmi_server.server_close()
        modbus_server.stop()
        modbus_greenlet.kill()
        hmi_greenlet.kill()
        print("agentic PLC honeypot stopped", flush=True)
    return 0


def _prepare_conpot_runtime_dirs(repo_root: Path) -> None:
    (repo_root / "tests" / "data" / "data_temp_fs").mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
