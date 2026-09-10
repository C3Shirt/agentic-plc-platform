from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class Intent(StrEnum):
    DISCOVER = "discover"
    READ_PROCESS = "read_process"
    READ_IDENTITY = "read_identity"
    WRITE_SETPOINT = "write_setpoint"
    CONTROL_OUTPUT = "control_output"
    INVALID_ADDRESS = "invalid_address"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    AUTH_ATTEMPT = "auth_attempt"
    FILE_TRANSFER = "file_transfer"


@dataclass(frozen=True, slots=True)
class ICSEvent:
    protocol: str
    session_id: str
    source_ip: str
    intent: Intent
    operation: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    actor_id: str | None = None
    source_port: int | None = None
    transaction_id: str | None = None
    unit_id: int | None = None
    address: int | None = None
    count: int | None = None
    previous_value: Any = None
    requested_value: Any = None
    resulting_value: Any = None
    result: str = "observed"
    world_revision: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeceptionPlan:
    actor_id: str
    action: str
    target: str
    reason: str
    ttl_seconds: int = 300
    parameters: dict[str, Any] = field(default_factory=dict)

