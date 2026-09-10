from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class SessionContext:
    protocol: str
    session_id: str
    source_ip: str
    source_port: int | None = None
    destination_ip: str | None = None
    destination_port: int | None = None


class ActorCorrelator:
    """Deterministically maps connection/session context to actor identifiers."""

    def actor_id_for(self, context: SessionContext) -> str:
        source_ip = context.source_ip.strip()
        if not source_ip or source_ip == "0.0.0.0":
            return "actor:unknown"
        digest = sha256(source_ip.encode("utf-8")).hexdigest()[:16]
        return f"actor:{digest}"

    def actor_key_for(self, context: SessionContext) -> str:
        return context.source_ip.strip() or "unknown"
