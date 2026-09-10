from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from agentic_plc.agent import (
    AgentController,
    LLMConfig,
    OpenAICompatiblePlanner,
    RuleBasedDeceptionPlanner,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.telemetry import JsonlEventStore, event_to_dict, summarize_events
from agentic_plc.world import TankPumpWorld


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the bounded deception agent.")
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Call the configured OpenAI-compatible API from .env.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=Path("records") / "agent_smoke_events.jsonl",
    )
    parser.add_argument(
        "--model",
        help="Override the .env model for --live-llm without printing secrets.",
    )
    args = parser.parse_args()

    events = sample_events()
    store = JsonlEventStore(args.events)
    store.clear()
    store.append_many(events)

    config = LLMConfig.from_env(".env")
    if args.model:
        config = replace(config, model=args.model)
    if args.live_llm:
        planner = OpenAICompatiblePlanner(config)
        planner_name = "llm"
    else:
        planner = RuleBasedDeceptionPlanner()
        planner_name = "rule"

    world = TankPumpWorld()
    decision = AgentController(planner, world=world).run_once(store.iter_events())

    protocol_demo = AgentController(RuleBasedDeceptionPlanner()).run_once(
        sample_read_events()
    )
    output = {
        "event_log": str(args.events),
        "llm_configured": config.is_configured,
        "planner": planner_name,
        "summary": summarize_events(store.iter_events()).to_dict(),
        "accepted": [plan_to_dict(plan) for plan in decision.accepted],
        "protocol_replies": [
            protocol_reply_to_dict(reply) for reply in decision.protocol_replies
        ],
        "protocol_reply_demo": [
            protocol_reply_to_dict(reply) for reply in protocol_demo.protocol_replies
        ],
        "world_patches": [
            applied_world_patch_to_dict(patch) for patch in decision.world_patches
        ],
        "world_snapshot": world.snapshot(),
        "rejected": [
            {
                "kind": rejected.kind,
                "plan": object_to_dict(rejected.plan),
                "error": rejected.error,
            }
            for rejected in decision.rejected
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def sample_events() -> list[ICSEvent]:
    return [
        ICSEvent(
            protocol="modbus",
            session_id="session-1",
            source_ip="192.0.2.10",
            actor_id="actor-192.0.2.10",
            intent=Intent.WRITE_SETPOINT,
            operation="write_holding_registers",
            address=0,
            requested_value=700,
            resulting_value=700,
            result="accepted",
            world_revision=2,
        ),
        ICSEvent(
            protocol="modbus",
            session_id="session-1",
            source_ip="192.0.2.10",
            actor_id="actor-192.0.2.10",
            intent=Intent.WRITE_SETPOINT,
            operation="write_holding_registers",
            address=0,
            requested_value=720,
            resulting_value=720,
            result="accepted",
            world_revision=3,
        ),
    ]


def sample_read_events() -> list[ICSEvent]:
    return [
        ICSEvent(
            protocol="modbus",
            session_id="session-2",
            source_ip="192.0.2.10",
            actor_id="actor-192.0.2.10",
            intent=Intent.READ_PROCESS,
            operation="read_holding_registers",
            transaction_id="17",
            unit_id=1,
            address=0,
            count=2,
            result="observed",
            world_revision=3,
        )
    ]


def plan_to_dict(plan) -> dict[str, object]:
    return {
        "actor_id": plan.actor_id,
        "action": plan.action,
        "target": plan.target,
        "reason": plan.reason,
        "ttl_seconds": plan.ttl_seconds,
        "parameters": plan.parameters,
    }


def protocol_reply_to_dict(reply) -> dict[str, object]:
    return {
        "protocol": reply.protocol,
        "payload_hex": reply.payload_hex,
        "reason": reply.reason,
        "transaction_id": reply.transaction_id,
        "unit_id": reply.unit_id,
        "metadata": reply.metadata,
    }


def applied_world_patch_to_dict(patch) -> dict[str, object]:
    return {
        "patch": object_to_dict(patch.patch),
        "before": patch.before,
        "after": patch.after,
    }


def object_to_dict(value) -> dict[str, object]:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return {"repr": repr(value)}


if __name__ == "__main__":
    raise SystemExit(main())
