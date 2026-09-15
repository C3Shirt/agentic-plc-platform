from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agentic_plc.contracts.actions import WorldPatch, WorldPatchOperation
from agentic_plc.world.patch import AppliedWorldPatch

from .base import (
    ProcessBackend,
    ProcessSnapshot,
    ProcessVariable,
    ReadOnlyProcessVariable,
    UnknownProcessVariable,
)
from .snapshot import ProcessSnapshotManager


@dataclass(frozen=True, slots=True)
class PhysicalProcessAgentMemoryEntry:
    """One compact, auditable memory item for the physical-process agent."""

    kind: str
    revision: int
    simulated_seconds: float
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "revision": self.revision,
            "simulated_seconds": self.simulated_seconds,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class PhysicalProcessAgentContext:
    """Prompt/policy-facing context for proposing physical-process evolution."""

    snapshot: ProcessSnapshot
    variables: Mapping[str, ProcessVariable]
    memory: tuple[PhysicalProcessAgentMemoryEntry, ...]
    elapsed_seconds: float

    def read(self, variable_id: str) -> float:
        return self.snapshot.read(variable_id)


class PhysicalProcessAgentPolicy(Protocol):
    """Produces simulator-internal process-state patches from a snapshot."""

    def propose(self, context: PhysicalProcessAgentContext) -> WorldPatch | None:
        ...


class NoOpPhysicalProcessAgentPolicy:
    """Baseline policy for wrapping a backend without changing its dynamics."""

    def propose(self, context: PhysicalProcessAgentContext) -> WorldPatch | None:
        return None


@dataclass(frozen=True, slots=True)
class SetpointTrackingRule:
    """Generic first-order rule: move one measurement toward one setpoint."""

    measurement: str
    setpoint: str
    gain_per_second: float = 0.2
    actuator: str | None = None
    actuator_gain_per_second: float = 0.0
    deadband: float = 0.0
    max_delta_per_second: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.measurement.strip():
            raise ValueError("measurement is required")
        if not self.setpoint.strip():
            raise ValueError("setpoint is required")
        if self.gain_per_second < 0:
            raise ValueError("gain_per_second must be non-negative")
        if self.deadband < 0:
            raise ValueError("deadband must be non-negative")
        if self.max_delta_per_second is not None and self.max_delta_per_second <= 0:
            raise ValueError("max_delta_per_second must be positive")


class SetpointTrackingProcessAgentPolicy:
    """Deterministic, simulator-agnostic physical-process agent baseline.

    It is deliberately simple: the policy keeps observable measurements moving
    plausibly toward exposed setpoints, optionally influenced by an actuator.
    This gives the honeypot a process-aware baseline that can later be replaced
    by an LLM policy without changing the backend/snapshot contract.
    """

    def __init__(
        self,
        rules: Iterable[SetpointTrackingRule],
        *,
        actor_id: str = "physical-process-agent",
        ttl_seconds: int = 60,
    ) -> None:
        self.rules = tuple(rules)
        if not self.rules:
            raise ValueError("at least one setpoint tracking rule is required")
        if not actor_id.strip():
            raise ValueError("actor_id is required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.actor_id = actor_id
        self.ttl_seconds = ttl_seconds

    def propose(self, context: PhysicalProcessAgentContext) -> WorldPatch | None:
        operations: list[WorldPatchOperation] = []

        for rule in self.rules:
            current = context.read(rule.measurement)
            target = context.read(rule.setpoint)
            error = target - current
            if abs(error) <= rule.deadband:
                continue

            delta = rule.gain_per_second * error * context.elapsed_seconds
            if rule.actuator is not None:
                delta += (
                    rule.actuator_gain_per_second
                    * context.read(rule.actuator)
                    * context.elapsed_seconds
                )
            if rule.max_delta_per_second is not None:
                limit = rule.max_delta_per_second * context.elapsed_seconds
                delta = max(-limit, min(limit, delta))

            if delta == 0.0:
                continue
            operations.append(
                WorldPatchOperation(
                    path=rule.measurement,
                    value=current + delta,
                    reason=(
                        f"Track {rule.setpoint}: current={current:.6g}, "
                        f"target={target:.6g}, dt={context.elapsed_seconds:.6g}."
                    ),
                )
            )

        if not operations:
            return None

        snapshot = context.snapshot
        return WorldPatch(
            actor_id=self.actor_id,
            reason="Advance physical-process measurements from snapshot state.",
            ttl_seconds=self.ttl_seconds,
            operations=operations,
            metadata={
                "base_revision": snapshot.revision,
                "process_id": snapshot.process_id,
                "backend_name": snapshot.backend_name,
                "patch_scope": "internal_process_dynamics",
                "policy": "setpoint_tracking",
                "elapsed_seconds": context.elapsed_seconds,
            },
        )


class AgenticProcessBackend:
    """Wrap any ProcessBackend with a physical-process agent policy.

    The wrapped backend remains authoritative. This class adds a snapshot-aware
    internal actor that can update read-only measurements through
    `ProcessSnapshotManager.apply_internal()`, while external writes still use
    the original backend's `write()` permissions.
    """

    def __init__(
        self,
        base_backend: ProcessBackend,
        *,
        policy: PhysicalProcessAgentPolicy | None = None,
        snapshot_manager: ProcessSnapshotManager | None = None,
        name: str | None = None,
        tick_base: bool = True,
        memory_size: int = 64,
    ) -> None:
        if memory_size <= 0:
            raise ValueError("memory_size must be positive")
        self._base_backend = base_backend
        self._policy = policy or NoOpPhysicalProcessAgentPolicy()
        self._snapshot_manager = snapshot_manager or ProcessSnapshotManager()
        self._tick_base = tick_base
        self._memory: deque[PhysicalProcessAgentMemoryEntry] = deque(
            maxlen=memory_size
        )
        self._last_agent_patch: AppliedWorldPatch | None = None
        self.process_id = base_backend.process_id
        self.name = name or f"agentic:{base_backend.name}"

    @property
    def base_backend(self) -> ProcessBackend:
        return self._base_backend

    @property
    def snapshot_manager(self) -> ProcessSnapshotManager:
        return self._snapshot_manager

    @property
    def memory(self) -> tuple[PhysicalProcessAgentMemoryEntry, ...]:
        return tuple(self._memory)

    @property
    def last_agent_patch(self) -> AppliedWorldPatch | None:
        return self._last_agent_patch

    @property
    def variables(self) -> Mapping[str, ProcessVariable]:
        return self._base_backend.variables

    def reset(self) -> None:
        self._base_backend.reset()
        self._memory.clear()
        self._last_agent_patch = None

    def snapshot(self) -> ProcessSnapshot:
        base_snapshot = self._base_backend.snapshot()
        metadata = dict(base_snapshot.metadata)
        metadata["agentic_wrapper"] = {
            "base_backend_name": base_snapshot.backend_name,
            "policy": type(self._policy).__name__,
            "memory_count": len(self._memory),
            "tick_base": self._tick_base,
        }
        return ProcessSnapshot(
            process_id=base_snapshot.process_id,
            backend_name=self.name,
            revision=base_snapshot.revision,
            simulated_seconds=base_snapshot.simulated_seconds,
            measurements=dict(base_snapshot.measurements),
            manipulated_variables=dict(base_snapshot.manipulated_variables),
            setpoints=dict(base_snapshot.setpoints),
            metadata=metadata,
        )

    def read(self, variable_id: str) -> float:
        return self._base_backend.read(variable_id)

    def write(self, variable_id: str, value: float) -> None:
        self._base_backend.write(variable_id, value)
        snapshot = self.snapshot()
        self._remember(
            snapshot,
            "external_write",
            {"variable_id": variable_id, "value": float(value)},
        )

    def write_internal(self, variable_id: str, value: float) -> None:
        variables = self._base_backend.variables
        if variable_id not in variables:
            raise UnknownProcessVariable(variable_id)
        writer = getattr(self._base_backend, "write_internal", None)
        if callable(writer):
            writer(variable_id, value)
            return
        if variables[variable_id].writable:
            self._base_backend.write(variable_id, value)
            return
        raise ReadOnlyProcessVariable(variable_id)

    def tick(self, seconds: float = 1.0) -> None:
        dt = float(seconds)
        if dt <= 0:
            raise ValueError("seconds must be positive")

        if self._tick_base:
            before = self.snapshot()
            self._base_backend.tick(dt)
            self._remember(
                self.snapshot(),
                "base_tick",
                {
                    "elapsed_seconds": dt,
                    "before_revision": before.revision,
                },
            )

        context = PhysicalProcessAgentContext(
            snapshot=self.snapshot(),
            variables=self.variables,
            memory=self.memory,
            elapsed_seconds=dt,
        )
        patch = self._policy.propose(context)
        if patch is None:
            self._last_agent_patch = None
            self._remember(
                self.snapshot(),
                "agent_noop",
                {"elapsed_seconds": dt},
            )
            return

        applied = self._snapshot_manager.apply_internal(self, patch)
        self._last_agent_patch = applied
        self._remember(
            self.snapshot(),
            "agent_patch",
            {
                "elapsed_seconds": dt,
                "changed_variables": [
                    operation.path for operation in patch.operations
                ],
                "operation_count": len(patch.operations),
                "policy": patch.metadata.get("policy"),
            },
        )

    def _remember(
        self,
        snapshot: ProcessSnapshot,
        kind: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._memory.append(
            PhysicalProcessAgentMemoryEntry(
                kind=kind,
                revision=snapshot.revision,
                simulated_seconds=snapshot.simulated_seconds,
                payload=dict(payload),
            )
        )
