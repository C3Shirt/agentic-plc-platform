from __future__ import annotations

import unittest

from agentic_plc.contracts.actions import WorldPatch, WorldPatchOperation
from agentic_plc.processes import (
    AgenticProcessBackend,
    FormulaProcessBackend,
    ProcessSnapshotManager,
    ProcessVariable,
    ReadOnlyProcessVariable,
    SetpointTrackingProcessAgentPolicy,
    SetpointTrackingRule,
)
from agentic_plc.world.patch import WorldPatchError


class AgenticProcessBackendTests(unittest.TestCase):
    def test_external_patch_cannot_write_read_only_measurement(self) -> None:
        backend = _state_store_backend()
        manager = ProcessSnapshotManager()

        with self.assertRaisesRegex(WorldPatchError, "read-only"):
            manager.apply(
                backend,
                WorldPatch(
                    actor_id="attacker-facing-agent",
                    reason="external patch should not change sensor state",
                    ttl_seconds=60,
                    operations=[
                        WorldPatchOperation(
                            path="level_pct",
                            value=55.0,
                            reason="try to rewrite a measurement",
                        )
                    ],
                    metadata={
                        "base_revision": 0,
                        "process_id": "agentic_tank_process",
                        "backend_name": "agent_state_store",
                    },
                ),
            )

        self.assertEqual(backend.read("level_pct"), 48.0)

    def test_internal_patch_can_evolve_read_only_measurement(self) -> None:
        backend = _state_store_backend()
        manager = ProcessSnapshotManager()

        applied = manager.apply_internal(
            backend,
            WorldPatch(
                actor_id="physical-process-agent",
                reason="internal dynamics update measurement",
                ttl_seconds=60,
                operations=[
                    WorldPatchOperation(
                        path="level_pct",
                        value=60.0,
                        reason="sensor-facing process state evolved",
                    )
                ],
                metadata={
                    "base_revision": 0,
                    "process_id": "agentic_tank_process",
                    "backend_name": "agent_state_store",
                    "patch_scope": "internal_process_dynamics",
                },
            ),
        )

        self.assertEqual(applied.before["revision"], 0)
        self.assertEqual(applied.after["revision"], 1)
        self.assertEqual(backend.read("level_pct"), 60.0)
        self.assertEqual(manager.records[-1].reason, "accepted_internal_world_patch")

    def test_agentic_wrapper_tracks_setpoint_with_auditable_memory(self) -> None:
        base_backend = _state_store_backend()
        backend = AgenticProcessBackend(
            base_backend,
            name="agentic:agent_state_store",
            policy=SetpointTrackingProcessAgentPolicy(
                [
                    SetpointTrackingRule(
                        measurement="level_pct",
                        setpoint="level_sp",
                        gain_per_second=0.1,
                        max_delta_per_second=5.0,
                    )
                ]
            ),
        )

        with self.assertRaises(ReadOnlyProcessVariable):
            backend.write("level_pct", 55.0)

        backend.tick(1.0)

        snapshot = backend.snapshot()
        self.assertEqual(snapshot.backend_name, "agentic:agent_state_store")
        self.assertEqual(snapshot.revision, 2)
        self.assertEqual(snapshot.simulated_seconds, 1.0)
        self.assertAlmostEqual(snapshot.read("level_pct"), 50.2)
        self.assertEqual(snapshot.metadata["agentic_wrapper"]["base_backend_name"], "agent_state_store")
        self.assertEqual(
            backend.snapshot_manager.records[-1].reason,
            "accepted_internal_world_patch",
        )
        self.assertEqual(
            backend.snapshot_manager.transitions[-1].changed_variables,
            ("level_pct",),
        )
        self.assertEqual(
            [entry.kind for entry in backend.memory],
            ["base_tick", "agent_patch"],
        )
        self.assertIsNotNone(backend.last_agent_patch)


def _state_store_backend() -> FormulaProcessBackend:
    return FormulaProcessBackend(
        process_id="agentic_tank_process",
        name="agent_state_store",
        variables=[
            ProcessVariable(
                variable_id="level_pct",
                name="Tank level",
                role="measurement",
                unit="%",
                minimum=0.0,
                maximum=100.0,
            ),
            ProcessVariable(
                variable_id="level_sp",
                name="Level setpoint",
                role="setpoint",
                unit="%",
                minimum=0.0,
                maximum=100.0,
                writable=True,
            ),
        ],
        initial_state={
            "level_pct": 48.0,
            "level_sp": 70.0,
        },
        equations=[],
        metadata={"simulator_family": "agent_state_store"},
    )


if __name__ == "__main__":
    unittest.main()
