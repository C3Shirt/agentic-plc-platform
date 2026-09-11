from __future__ import annotations

from agentic_plc.agent.planner import DeceptionPlanner, RuleBasedDeceptionPlanner
from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.agent.protocol_interaction import InteractionPhase
from agentic_plc.contracts.actions import AgentProposal
from agentic_plc.contracts.events import DeceptionPlan, ICSEvent


class ProcessAwareResponsePolicy:
    """Planner wrapper that adds phase-aware deception plans to protocol replies.

    This transfers the MANTIS-style idea of stateful multi-turn interaction into
    industrial protocol traffic. It does not implement an SSH surface.
    """

    def __init__(self, base_planner: DeceptionPlanner | None = None) -> None:
        self._base_planner = base_planner or RuleBasedDeceptionPlanner()

    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal | None:
        if not events:
            return None

        base = _as_proposal(self._base_planner.propose(events, context))
        phase_plan = self._phase_plan(events[-1], context)
        if base is None:
            return AgentProposal(deception_plan=phase_plan) if phase_plan else None
        if base.deception_plan is not None or phase_plan is None:
            return base
        return AgentProposal(
            deception_plan=phase_plan,
            protocol_reply=base.protocol_reply,
            world_patch=base.world_patch,
        )

    def _phase_plan(
        self,
        latest: ICSEvent,
        context: PhysicalProcessContext | None,
    ) -> DeceptionPlan | None:
        phase = latest.metadata.get("interaction_phase")
        if phase is None:
            return None

        try:
            interaction_phase = InteractionPhase(str(phase))
        except ValueError:
            return None

        actor_id = latest.actor_id or f"ip:{latest.source_ip}"
        process_id = context.process_id if context is not None else None
        common_parameters = {
            "interaction_phase": interaction_phase.value,
            "protocol": latest.protocol,
            "operation": latest.operation,
            "process_id": process_id,
            "reason_code": latest.metadata.get("interaction_phase_reason"),
        }

        if interaction_phase is InteractionPhase.ADDRESS_PROBING:
            return DeceptionPlan(
                actor_id=actor_id,
                action="expose_existing_artifact",
                target="docs/register_map_excerpt.txt",
                reason=(
                    "Protocol traffic suggests address discovery; prepare a "
                    "bounded register-map lure."
                ),
                ttl_seconds=600,
                parameters=common_parameters,
            )

        if interaction_phase is InteractionPhase.REGISTER_MAPPING:
            return DeceptionPlan(
                actor_id=actor_id,
                action="expose_existing_artifact",
                target="docs/plc_io_mapping_partial.csv",
                reason=(
                    "Actor is reading enough unique addresses to infer the PLC "
                    "map; expose a partial, process-consistent I/O map."
                ),
                ttl_seconds=900,
                parameters=common_parameters,
            )

        if interaction_phase is InteractionPhase.WRITE_ATTEMPT:
            return DeceptionPlan(
                actor_id=actor_id,
                action="publish_maintenance_note",
                target="WO-2048",
                reason=(
                    "Actor attempted to manipulate a process-facing register; "
                    "surface a plausible controller-tuning maintenance note."
                ),
                ttl_seconds=900,
                parameters={**common_parameters, "topic": "controller tuning"},
            )

        if interaction_phase is InteractionPhase.EFFECT_VERIFICATION:
            return DeceptionPlan(
                actor_id=actor_id,
                action="adjust_noncritical_narrative",
                target="process_response_delay",
                reason=(
                    "Actor appears to verify whether a write affected the "
                    "process; keep the narrative consistent with delayed plant "
                    "response."
                ),
                ttl_seconds=300,
                parameters=common_parameters,
            )

        if interaction_phase is InteractionPhase.FAULT_PROBING:
            return DeceptionPlan(
                actor_id=actor_id,
                action="select_existing_fault_scenario",
                target="faults/noncritical_sensor_drift",
                reason=(
                    "Actor is probing artifacts or fault context; select a "
                    "bounded noncritical fault story."
                ),
                ttl_seconds=900,
                parameters=common_parameters,
            )

        return None


class ProtocolStrategyPlanner(ProcessAwareResponsePolicy):
    """Semantic alias for the protocol-phase-aware planner wrapper."""


def _as_proposal(
    proposal: AgentProposal | DeceptionPlan | None,
) -> AgentProposal | None:
    if proposal is None:
        return None
    if isinstance(proposal, DeceptionPlan):
        return AgentProposal(deception_plan=proposal)
    return proposal
