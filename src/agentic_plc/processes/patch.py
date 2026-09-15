from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentic_plc.contracts.actions import WorldPatch
from agentic_plc.processes.base import ProcessBackend
from agentic_plc.world.patch import AppliedWorldPatch, WorldPatchError


@dataclass(frozen=True, slots=True)
class ProcessPatchPolicy:
    max_operations: int = 8
    max_ttl_seconds: int = 3600


@runtime_checkable
class _InternalWritableProcessBackend(Protocol):
    def write_internal(self, variable_id: str, value: float) -> None:
        """Update process state from inside the simulator/physical-process agent."""


class ProcessPatchApplier:
    """Validates and applies agent-generated patches to a ProcessBackend."""

    def __init__(self, policy: ProcessPatchPolicy | None = None) -> None:
        self._policy = policy or ProcessPatchPolicy()

    def validate(
        self,
        backend: ProcessBackend,
        patch: WorldPatch,
        *,
        allow_read_only: bool = False,
    ) -> None:
        if not patch.actor_id.strip():
            raise WorldPatchError("process patch actor_id is required")
        if not patch.reason.strip():
            raise WorldPatchError("process patch reason is required")
        if not 1 <= patch.ttl_seconds <= self._policy.max_ttl_seconds:
            raise WorldPatchError(
                f"process patch ttl_seconds must be between 1 and {self._policy.max_ttl_seconds}"
            )
        if not patch.operations:
            raise WorldPatchError("process patch requires at least one operation")
        if len(patch.operations) > self._policy.max_operations:
            raise WorldPatchError(
                f"process patch supports at most {self._policy.max_operations} operations"
            )

        variables = backend.variables
        for operation in patch.operations:
            if operation.path not in variables:
                raise WorldPatchError(
                    f"process patch path is not exposed by backend: {operation.path}"
                )
            variable = variables[operation.path]
            if not variable.writable and not allow_read_only:
                raise WorldPatchError(
                    f"process patch path is read-only: {operation.path}"
                )
            if (
                not variable.writable
                and allow_read_only
                and not isinstance(backend, _InternalWritableProcessBackend)
            ):
                raise WorldPatchError(
                    "backend does not support internal writes to read-only "
                    f"process path: {operation.path}"
                )
            value = float(operation.value)
            if variable.minimum is not None and value < variable.minimum:
                raise WorldPatchError(
                    f"{operation.path} must be >= {variable.minimum}"
                )
            if variable.maximum is not None and value > variable.maximum:
                raise WorldPatchError(
                    f"{operation.path} must be <= {variable.maximum}"
                )

    def apply(self, backend: ProcessBackend, patch: WorldPatch) -> AppliedWorldPatch:
        self.validate(backend, patch)
        before = backend.snapshot().to_dict()
        for operation in patch.operations:
            backend.write(operation.path, float(operation.value))
        after = backend.snapshot().to_dict()
        return AppliedWorldPatch(patch=patch, before=before, after=after)

    def apply_internal(
        self,
        backend: ProcessBackend,
        patch: WorldPatch,
    ) -> AppliedWorldPatch:
        """Apply a simulator-internal patch, including read-only measurements.

        This path is intentionally separate from `apply()`: protocol handlers and
        LLM response planners keep the normal writable-only surface, while a
        physical-process simulator/agent can update sensor-like state variables
        through an explicit `write_internal` backend hook.
        """

        self.validate(backend, patch, allow_read_only=True)
        before = backend.snapshot().to_dict()
        for operation in patch.operations:
            variable = backend.variables[operation.path]
            value = float(operation.value)
            if isinstance(backend, _InternalWritableProcessBackend):
                backend.write_internal(operation.path, value)
                continue
            if variable.writable:
                backend.write(operation.path, value)
                continue
            raise WorldPatchError(
                "backend does not support internal writes to read-only "
                f"process path: {operation.path}"
            )
        after = backend.snapshot().to_dict()
        return AppliedWorldPatch(patch=patch, before=before, after=after)
