from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Iterable

from agentic_plc.contracts.actions import WorldPatch
from agentic_plc.world.patch import AppliedWorldPatch, WorldPatchError

from .base import ProcessBackend, ProcessSnapshot
from .patch import ProcessPatchApplier


_REVISION_KEYS = ("base_revision", "expected_revision", "snapshot_revision")


@dataclass(frozen=True, slots=True)
class ProcessSnapshotRecord:
    """Auditable point-in-time record of the authoritative process state."""

    snapshot: ProcessSnapshot
    reason: str = "capture"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "snapshot": self.snapshot.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProcessSnapshotTransition:
    """Audit record for one accepted process-state mutation."""

    patch: WorldPatch
    before: ProcessSnapshot
    after: ProcessSnapshot
    changed_variables: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch": {
                "actor_id": self.patch.actor_id,
                "reason": self.patch.reason,
                "ttl_seconds": self.patch.ttl_seconds,
                "operations": [
                    {
                        "path": operation.path,
                        "value": operation.value,
                        "reason": operation.reason,
                    }
                    for operation in self.patch.operations
                ],
                "metadata": dict(self.patch.metadata),
            },
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "changed_variables": list(self.changed_variables),
        }


class ProcessSnapshotManager:
    """Owns snapshot preconditions and audit history for process backends.

    The backend remains the authoritative state holder. This manager adds the
    missing governance layer around agent-generated patches: it checks that a
    patch was produced against the current revision, validates the process
    surface, applies the patch, and records before/after snapshots.
    """

    def __init__(
        self,
        patch_applier: ProcessPatchApplier | None = None,
        *,
        history_size: int = 64,
    ) -> None:
        if history_size <= 0:
            raise ValueError("history_size must be positive")
        self._patch_applier = patch_applier or ProcessPatchApplier()
        self._records: deque[ProcessSnapshotRecord] = deque(maxlen=history_size)
        self._transitions: deque[ProcessSnapshotTransition] = deque(
            maxlen=history_size
        )

    @property
    def records(self) -> tuple[ProcessSnapshotRecord, ...]:
        return tuple(self._records)

    @property
    def transitions(self) -> tuple[ProcessSnapshotTransition, ...]:
        return tuple(self._transitions)

    def capture(
        self,
        backend: ProcessBackend,
        *,
        reason: str = "capture",
        metadata: dict[str, Any] | None = None,
    ) -> ProcessSnapshotRecord:
        record = ProcessSnapshotRecord(
            snapshot=backend.snapshot(),
            reason=reason,
            metadata=dict(metadata or {}),
        )
        self._records.append(record)
        return record

    def apply(self, backend: ProcessBackend, patch: WorldPatch) -> AppliedWorldPatch:
        before = backend.snapshot()
        self.validate_patch_preconditions(backend, patch, before=before)
        applied = self._patch_applier.apply(backend, patch)
        after = backend.snapshot()
        self._transitions.append(
            ProcessSnapshotTransition(
                patch=patch,
                before=before,
                after=after,
                changed_variables=tuple(operation.path for operation in patch.operations),
            )
        )
        self._records.append(
            ProcessSnapshotRecord(
                snapshot=after,
                reason="accepted_world_patch",
                metadata={
                    "actor_id": patch.actor_id,
                    "operation_count": len(patch.operations),
                    "base_revision": before.revision,
                    "after_revision": after.revision,
                },
            )
        )
        return applied

    def apply_internal(
        self,
        backend: ProcessBackend,
        patch: WorldPatch,
    ) -> AppliedWorldPatch:
        """Apply a simulator-internal process mutation and record its snapshot.

        Unlike `apply()`, this method allows read-only process variables to be
        updated through a backend's `write_internal` hook. It is meant for
        physical-process dynamics, not for attacker-facing protocol writes or
        unconstrained LLM world patches.
        """

        before = backend.snapshot()
        self.validate_patch_preconditions(
            backend,
            patch,
            before=before,
            allow_read_only=True,
        )
        applied = self._patch_applier.apply_internal(backend, patch)
        after = backend.snapshot()
        self._transitions.append(
            ProcessSnapshotTransition(
                patch=patch,
                before=before,
                after=after,
                changed_variables=tuple(operation.path for operation in patch.operations),
            )
        )
        self._records.append(
            ProcessSnapshotRecord(
                snapshot=after,
                reason="accepted_internal_world_patch",
                metadata={
                    "actor_id": patch.actor_id,
                    "operation_count": len(patch.operations),
                    "base_revision": before.revision,
                    "after_revision": after.revision,
                    "patch_scope": "internal_process_dynamics",
                },
            )
        )
        return applied

    def validate_patch_preconditions(
        self,
        backend: ProcessBackend,
        patch: WorldPatch,
        *,
        before: ProcessSnapshot | None = None,
        allow_read_only: bool = False,
    ) -> None:
        snapshot = before or backend.snapshot()
        metadata = patch.metadata

        process_id = metadata.get("process_id")
        if process_id is not None and str(process_id) != snapshot.process_id:
            raise WorldPatchError(
                f"process patch process_id mismatch: {process_id} != {snapshot.process_id}"
            )

        backend_name = metadata.get("backend_name")
        if backend_name is not None and str(backend_name) != snapshot.backend_name:
            raise WorldPatchError(
                "process patch backend_name mismatch: "
                f"{backend_name} != {snapshot.backend_name}"
            )

        expected_revision = _first_int(metadata, _REVISION_KEYS)
        if expected_revision is not None and expected_revision != snapshot.revision:
            raise WorldPatchError(
                "stale process patch: "
                f"expected revision {expected_revision}, current revision {snapshot.revision}"
            )

        _validate_no_duplicate_paths(operation.path for operation in patch.operations)
        _validate_finite_numeric_values(patch)
        self._patch_applier.validate(
            backend,
            patch,
            allow_read_only=allow_read_only,
        )

    @staticmethod
    def snapshot_card(
        snapshot: ProcessSnapshot,
        *,
        max_values_per_group: int = 8,
    ) -> dict[str, Any]:
        """Return a compact prompt-facing card for the authoritative snapshot."""

        return {
            "process_id": snapshot.process_id,
            "backend_name": snapshot.backend_name,
            "revision": snapshot.revision,
            "simulated_seconds": snapshot.simulated_seconds,
            "measurement_count": len(snapshot.measurements),
            "manipulated_variable_count": len(snapshot.manipulated_variables),
            "setpoint_count": len(snapshot.setpoints),
            "measurements": _first_items(snapshot.measurements, max_values_per_group),
            "manipulated_variables": _first_items(
                snapshot.manipulated_variables,
                max_values_per_group,
            ),
            "setpoints": _first_items(snapshot.setpoints, max_values_per_group),
            "metadata": {
                key: value
                for key, value in snapshot.metadata.items()
                if key not in {"overrides"}
            },
        }


def _first_int(metadata: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise WorldPatchError(f"process patch {key} must be an integer") from exc
    return None


def _validate_no_duplicate_paths(paths: Iterable[str]) -> None:
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            raise WorldPatchError(f"process patch repeats path: {path}")
        seen.add(path)


def _validate_finite_numeric_values(patch: WorldPatch) -> None:
    for operation in patch.operations:
        try:
            value = float(operation.value)
        except (TypeError, ValueError) as exc:
            raise WorldPatchError(
                f"process patch value for {operation.path} must be numeric"
            ) from exc
        if not isfinite(value):
            raise WorldPatchError(
                f"process patch value for {operation.path} must be finite"
            )


def _first_items(values: Any, limit: int) -> dict[str, float]:
    if limit <= 0:
        return {}
    return {
        key: float(value)
        for key, value in list(dict(values).items())[:limit]
    }
