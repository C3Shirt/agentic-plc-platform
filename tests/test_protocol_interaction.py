from __future__ import annotations

import json
import unittest

from agentic_plc.agent import (
    AgentRuntime,
    InteractionPhase,
    LLMConfig,
    OpenAICompatiblePlanner,
    ProcessAwareResponsePolicy,
    ProtocolIntentTracker,
    RuleBasedDeceptionPlanner,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.telemetry import InMemoryEventLog
from agentic_plc.world import TankPumpWorld


class ProtocolInteractionTests(unittest.TestCase):
    def test_tracker_detects_register_mapping_from_many_unique_reads(self) -> None:
        tracker = ProtocolIntentTracker()
        enriched = None
        for address in range(5):
            _, enriched = tracker.observe(_event(Intent.READ_PROCESS, address=address))

        assert enriched is not None
        self.assertEqual(
            enriched.metadata["interaction_phase"],
            InteractionPhase.REGISTER_MAPPING.value,
        )
        self.assertEqual(
            enriched.metadata["interaction_phase_reason"],
            "many_unique_protocol_addresses",
        )
        self.assertEqual(enriched.metadata["actor_protocol_unique_touched_addresses"], 5)

    def test_tracker_detects_effect_verification_after_write_then_read(self) -> None:
        tracker = ProtocolIntentTracker()
        tracker.observe(
            _event(
                Intent.CONTROL_OUTPUT,
                operation="write_single_register",
                address=5,
                count=1,
                requested_value=420,
                function_code=6,
            )
        )

        state, enriched = tracker.observe(
            _event(
                Intent.READ_PROCESS,
                operation="read_holding_registers",
                address=5,
                count=1,
                function_code=3,
            )
        )

        self.assertEqual(state.phase, InteractionPhase.EFFECT_VERIFICATION)
        self.assertEqual(
            enriched.metadata["interaction_phase"],
            InteractionPhase.EFFECT_VERIFICATION.value,
        )
        self.assertEqual(
            enriched.metadata["actor_protocol_last_write_range"]["address"],
            5,
        )

    def test_tracker_detects_address_probing_from_consecutive_errors(self) -> None:
        tracker = ProtocolIntentTracker()
        tracker.observe(_event(Intent.INVALID_ADDRESS, operation="read_invalid", address=99))
        state, enriched = tracker.observe(
            _event(Intent.UNSUPPORTED_OPERATION, operation="function_99", address=100)
        )

        self.assertEqual(state.phase, InteractionPhase.ADDRESS_PROBING)
        self.assertEqual(
            enriched.metadata["interaction_phase_reason"],
            "consecutive_invalid_or_unsupported",
        )

    def test_runtime_enriches_stored_events_and_policy_adds_phase_plan(self) -> None:
        event_log = InMemoryEventLog()
        runtime = AgentRuntime(world=TankPumpWorld(), event_store=event_log)

        decision = runtime.observe_and_decide(
            _event(
                Intent.CONTROL_OUTPUT,
                operation="write_single_register",
                address=1,
                count=1,
                requested_value=2,
                function_code=6,
            )
        )

        stored_event = event_log.list_events()[0]
        self.assertEqual(
            stored_event.metadata["interaction_phase"],
            InteractionPhase.WRITE_ATTEMPT.value,
        )
        self.assertEqual(len(decision.accepted), 1)
        self.assertEqual(decision.accepted[0].action, "publish_maintenance_note")
        self.assertEqual(
            decision.accepted[0].parameters["interaction_phase"],
            InteractionPhase.WRITE_ATTEMPT.value,
        )

    def test_process_aware_response_policy_preserves_generated_protocol_reply(self) -> None:
        policy = ProcessAwareResponsePolicy(RuleBasedDeceptionPlanner())

        proposal = policy.propose([
            _event(
                Intent.READ_PROCESS,
                operation="read_holding_registers",
                address=0,
                count=2,
                function_code=3,
                metadata_extra={
                    "interaction_phase": InteractionPhase.REGISTER_MAPPING.value,
                    "interaction_phase_reason": "many_unique_protocol_addresses",
                },
            )
        ])

        assert proposal is not None
        self.assertIsNotNone(proposal.protocol_reply)
        self.assertIsNotNone(proposal.deception_plan)
        self.assertEqual(proposal.deception_plan.action, "expose_existing_artifact")

    def test_planner_compact_event_includes_interaction_phase(self) -> None:
        planner = OpenAICompatiblePlanner(
            LLMConfig(
                base_url="https://example.test/v1",
                api_key="dummy",
                model="test-model",
            )
        )

        prompt = planner._user_prompt([
            _event(
                Intent.READ_PROCESS,
                address=0,
                metadata_extra={
                    "interaction_phase": InteractionPhase.PROCESS_MONITORING.value,
                    "interaction_phase_reason": "process_read",
                    "actor_protocol_event_count": 1,
                },
            )
        ])
        payload = json.loads(prompt.split("Payload: ", 1)[1])

        self.assertEqual(
            payload["events"][0]["metadata"]["interaction_phase"],
            InteractionPhase.PROCESS_MONITORING.value,
        )


def _event(
    intent: Intent,
    *,
    operation: str | None = None,
    address: int | None = None,
    count: int | None = 1,
    requested_value: int | None = None,
    function_code: int | None = 3,
    metadata_extra: dict[str, object] | None = None,
) -> ICSEvent:
    metadata: dict[str, object] = {}
    if function_code is not None:
        metadata["function_code"] = function_code
    metadata.update(metadata_extra or {})
    return ICSEvent(
        protocol="modbus",
        session_id="s1",
        source_ip="192.0.2.10",
        actor_id="actor-1",
        intent=intent,
        operation=operation or intent.value,
        transaction_id="17",
        unit_id=1,
        address=address,
        count=count,
        requested_value=requested_value,
        result="observed",
        metadata=metadata,
    )


if __name__ == "__main__":
    unittest.main()
