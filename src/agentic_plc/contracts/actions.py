from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_plc.contracts.events import DeceptionPlan


@dataclass(frozen=True, slots=True)
class ProtocolReply:
    """A generated protocol response candidate.

    The payload is intentionally kept as hex so an LLM can generate complete
    protocol bytes while the platform can still validate and audit the frame
    before any adapter sends it.
    """

    protocol: str
    payload_hex: str
    reason: str
    transaction_id: str | None = None
    unit_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorldPatchOperation:
    """A single proposed mutation against the authoritative world state."""

    path: str
    value: Any
    reason: str = ""


@dataclass(frozen=True, slots=True)
class WorldPatch:
    """A validated set of world-model mutations proposed by an agent."""

    actor_id: str
    reason: str
    operations: list[WorldPatchOperation] = field(default_factory=list)
    ttl_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentProposal:
    """The full action envelope an LLM or rule planner may return."""

    deception_plan: DeceptionPlan | None = None
    protocol_reply: ProtocolReply | None = None
    world_patch: WorldPatch | None = None
