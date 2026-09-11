from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from agentic_plc.agent.controller import AgentController, AgentDecision
from agentic_plc.agent.planner import DeceptionPlanner
from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.agent.protocol_interaction import (
    ActorProtocolState,
    ProtocolIntentTracker,
)
from agentic_plc.agent.response_policy import ProcessAwareResponsePolicy
from agentic_plc.contracts.events import ICSEvent
from agentic_plc.world.model import TankPumpWorld


class EventStoreLike(Protocol):
    def append(self, event: ICSEvent) -> None:
        raise NotImplementedError

    def iter_events(self) -> Iterable[ICSEvent]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    decision_window: int = 20
    enrich_protocol_state: bool = True


@dataclass(slots=True)
class AgentRuntime:
    """Small runtime that connects event intake to validated agent actions."""

    world: TankPumpWorld | None = None
    process_context: PhysicalProcessContext | None = None
    planner: DeceptionPlanner = field(default_factory=ProcessAwareResponsePolicy)
    event_store: EventStoreLike | None = None
    config: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    protocol_tracker: ProtocolIntentTracker = field(
        default_factory=ProtocolIntentTracker
    )
    decisions: list[AgentDecision] = field(default_factory=list)

    def observe(self, event: ICSEvent) -> ICSEvent:
        observed_event = event
        if self.config.enrich_protocol_state:
            _, observed_event = self.protocol_tracker.observe(event)
        if self.event_store is not None:
            self.event_store.append(observed_event)
        return observed_event

    def decide(self, events: Iterable[ICSEvent]) -> AgentDecision:
        event_list = list(events)[-self.config.decision_window :]
        decision = AgentController(
            self.planner,
            world=self.world,
            process_context=self.process_context,
        ).run_once(event_list)
        self.decisions.append(decision)
        return decision

    def observe_and_decide(self, event: ICSEvent) -> AgentDecision:
        observed_event = self.observe(event)
        if self.event_store is None:
            return self.decide([observed_event])
        return self.decide(self.event_store.iter_events())

    def latest_decision(self) -> AgentDecision | None:
        if not self.decisions:
            return None
        return self.decisions[-1]

    def protocol_state_for(
        self,
        actor_id: str,
        protocol: str,
    ) -> ActorProtocolState | None:
        return self.protocol_tracker.state_for(actor_id, protocol)
