from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.agent.planner import DeceptionPlanner, RuleBasedDeceptionPlanner
from agentic_plc.contracts.actions import AgentProposal, ProtocolReply, WorldPatch
from agentic_plc.contracts.events import DeceptionPlan, ICSEvent, Intent
from agentic_plc.policy.plan_validator import DeceptionPlanValidator
from agentic_plc.policy.protocol_reply_validator import ProtocolReplyValidator
from agentic_plc.processes import ProcessPatchApplier, ProcessSnapshotManager
from agentic_plc.protocols.modbus import ModbusFrameError, parse_modbus_tcp_frame
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
        process_patch_applier: ProcessPatchApplier | None = None,
        process_snapshot_manager: ProcessSnapshotManager | None = None,
        world: TankPumpWorld | None = None,
        process_context: PhysicalProcessContext | None = None,
    ) -> None:
        self._planner = planner or RuleBasedDeceptionPlanner()
        self._validator = validator or DeceptionPlanValidator()
        self._protocol_reply_validator = (
            protocol_reply_validator or ProtocolReplyValidator()
        )
        self._world_patch_applier = world_patch_applier or WorldPatchApplier()
        self._process_patch_applier = process_patch_applier or ProcessPatchApplier()
        self._process_snapshot_manager = (
            process_snapshot_manager
            or ProcessSnapshotManager(self._process_patch_applier)
        )
        self._world = world
        self._process_context = process_context

    def run_once(self, events: Iterable[ICSEvent]) -> AgentDecision:
        event_list = list(events)
        proposal = _normalize_proposal(
            _call_planner(self._planner, event_list, self._process_context)
        )
        if proposal is None:
            return AgentDecision()

        accepted: list[DeceptionPlan] = []
        protocol_replies: list[ProtocolReply] = []
        world_patches: list[AppliedWorldPatch] = []
        rejected: list[RejectedPlan] = []
        validated_protocol_reply: ProtocolReply | None = None
        reply_requires_patch = False
        expected_write_patch: WorldPatch | None = None

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
                validated_protocol_reply = proposal.protocol_reply
                reply_requires_patch = _reply_requires_accepted_world_patch(
                    validated_protocol_reply,
                    event_list,
                )
                if reply_requires_patch and self._process_context is not None:
                    expected_write_patch = _expected_process_write_patch(
                        self._process_context,
                        event_list,
                    )

        if proposal.world_patch is not None:
            if (
                reply_requires_patch
                and self._process_context is not None
                and not _patch_covers_expected(
                    proposal.world_patch,
                    expected_write_patch,
                )
            ):
                rejected.append(
                    RejectedPlan(
                        plan=proposal.world_patch,
                        error=(
                            "world patch does not match the latest mapped "
                            "protocol write"
                        ),
                        kind="world_patch",
                    )
                )
            elif self._process_context is not None:
                try:
                    world_patches.append(
                        self._process_snapshot_manager.apply(
                            self._process_context.backend,
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
            elif self._world is None:
                rejected.append(
                    RejectedPlan(
                        plan=proposal.world_patch,
                        error=(
                            "world patch requires AgentController(world=...) or "
                            "AgentController(process_context=...)"
                        ),
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

        if validated_protocol_reply is not None:
            if reply_requires_patch and not _accepted_patches_cover_expected(
                world_patches,
                expected_write_patch,
                self._process_context,
            ):
                rejected.append(
                    RejectedPlan(
                        plan=validated_protocol_reply,
                        error=(
                            "protocol write reply withheld because no paired "
                            "world patch was accepted"
                        ),
                        kind="protocol_reply",
                    )
                )
            else:
                protocol_replies.append(validated_protocol_reply)

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


def _call_planner(
    planner: DeceptionPlanner,
    events: list[ICSEvent],
    context: PhysicalProcessContext | None,
) -> AgentProposal | DeceptionPlan | None:
    try:
        return planner.propose(events, context)
    except TypeError as exc:
        try:
            return planner.propose(events)  # type: ignore[call-arg]
        except TypeError:
            raise exc


def _reply_requires_accepted_world_patch(
    reply: ProtocolReply,
    events: list[ICSEvent],
) -> bool:
    if not _latest_event_is_write(events):
        return False
    if bool(reply.metadata.get("requires_accepted_world_patch")):
        return True
    if reply.protocol.lower() not in {"modbus", "modbus_tcp"}:
        return False
    try:
        frame = parse_modbus_tcp_frame(reply.payload_hex)
    except ModbusFrameError:
        return False
    return not frame.is_exception and frame.function_code in {5, 6, 15, 16}


def _latest_event_is_write(events: list[ICSEvent]) -> bool:
    if not events:
        return False
    latest = events[-1]
    return latest.intent in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}


def _expected_process_write_patch(
    context: PhysicalProcessContext,
    events: list[ICSEvent],
) -> WorldPatch | None:
    if not events:
        return None
    try:
        return context.world_patch_for_modbus_write_event(events[-1])
    except ValueError:
        return None


def _accepted_patches_cover_expected(
    applied_patches: list[AppliedWorldPatch],
    expected_patch: WorldPatch | None,
    context: PhysicalProcessContext | None,
) -> bool:
    if not applied_patches:
        return False
    if context is None:
        return True
    return any(
        _patch_covers_expected(applied.patch, expected_patch)
        for applied in applied_patches
    )


def _patch_covers_expected(
    candidate: WorldPatch,
    expected: WorldPatch | None,
) -> bool:
    if expected is None:
        return False
    return all(
        any(
            operation.path == expected_operation.path
            and _same_patch_value(operation.value, expected_operation.value)
            for operation in candidate.operations
        )
        for expected_operation in expected.operations
    )


def _same_patch_value(left: object, right: object) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return left == right
