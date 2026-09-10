from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from agentic_plc.agent.planner import DeceptionPlanner, RuleBasedDeceptionPlanner
from agentic_plc.contracts.actions import AgentProposal, ProtocolReply, WorldPatch
from agentic_plc.contracts.events import DeceptionPlan, ICSEvent
from agentic_plc.policy.plan_validator import DeceptionPlanValidator
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.world.model import TankPumpWorld
from agentic_plc.world.patch import AppliedWorldPatch, WorldPatchApplier


@dataclass(frozen=True, slots=True)
class RejectedPlan:
    plan: object
    error: str
    kind: str = "deception_plan"


@dataclass(frozen=True, slots=True)
class AgentDecision:
    accepted: list[DeceptionPlan] = field(default_factory=list)
    protocol_replies: list[ProtocolReply] = field(default_factory=list)
    world_patches: list[AppliedWorldPatch] = field(default_factory=list)
    rejected: list[RejectedPlan] = field(default_factory=list)

    @property
    def has_action(self) -> bool:
        return bool(self.accepted or self.protocol_replies or self.world_patches)


class AgentController:
    """Runs one bounded planning pass over normalized honeypot events."""

    def __init__(
        self,
        planner: DeceptionPlanner | None = None,
        validator: DeceptionPlanValidator | None = None,
        protocol_reply_validator: ProtocolReplyValidator | None = None,
        world_patch_applier: WorldPatchApplier | None = None,
        world: TankPumpWorld | None = None,
    ) -> None:
        self._planner = planner or RuleBasedDeceptionPlanner()
        self._validator = validator or DeceptionPlanValidator()
        self._protocol_reply_validator = (
            protocol_reply_validator or ProtocolReplyValidator()
        )
        self._world_patch_applier = world_patch_applier or WorldPatchApplier()
        self._world = world

    def run_once(self, events: Iterable[ICSEvent]) -> AgentDecision:
        event_list = list(events)
        proposal = _normalize_proposal(self._planner.propose(event_list))
        if proposal is None:
            return AgentDecision()

        accepted: list[DeceptionPlan] = []
        protocol_replies: list[ProtocolReply] = []
        world_patches: list[AppliedWorldPatch] = []
        rejected: list[RejectedPlan] = []

        if proposal.deception_plan is not None:
            try:
                self._validator.validate(proposal.deception_plan)
            except ValueError as exc:
                rejected.append(
                    RejectedPlan(
                        plan=proposal.deception_plan,
                        error=str(exc),
                        kind="deception_plan",
                    )
                )
            else:
                accepted.append(proposal.deception_plan)

        if proposal.protocol_reply is not None:
            try:
                self._protocol_reply_validator.validate(
                    proposal.protocol_reply,
                    event_list,
                )
            except ValueError as exc:
                rejected.append(
                    RejectedPlan(
                        plan=proposal.protocol_reply,
                        error=str(exc),
                        kind="protocol_reply",
                    )
                )
            else:
                protocol_replies.append(proposal.protocol_reply)

        if proposal.world_patch is not None:
            if self._world is None:
                rejected.append(
                    RejectedPlan(
                        plan=proposal.world_patch,
                        error="world patch requires AgentController(world=...)",
                        kind="world_patch",
                    )
                )
            else:
                try:
                    world_patches.append(
                        self._world_patch_applier.apply(
                            self._world,
                            proposal.world_patch,
                        )
                    )
                except ValueError as exc:
                    rejected.append(
                        RejectedPlan(
                            plan=proposal.world_patch,
                            error=str(exc),
                            kind="world_patch",
                        )
                    )

        return AgentDecision(
            accepted=accepted,
            protocol_replies=protocol_replies,
            world_patches=world_patches,
            rejected=rejected,
        )


def _normalize_proposal(
    proposal: AgentProposal | DeceptionPlan | None,
) -> AgentProposal | None:
    if proposal is None:
        return None
    if isinstance(proposal, DeceptionPlan):
        return AgentProposal(deception_plan=proposal)
    return proposal
