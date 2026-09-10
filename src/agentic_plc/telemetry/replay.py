from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from agentic_plc.contracts.events import ICSEvent


@dataclass(frozen=True, slots=True)
class EventReplaySummary:
    total_events: int
    sessions: int
    actors: int
    protocols: dict[str, int]
    intents: dict[str, int]
    results: dict[str, int]
    first_timestamp: str | None
    last_timestamp: str | None
    latest_world_revision: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "total_events": self.total_events,
            "sessions": self.sessions,
            "actors": self.actors,
            "protocols": self.protocols,
            "intents": self.intents,
            "results": self.results,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "latest_world_revision": self.latest_world_revision,
        }


def summarize_events(events: Iterable[ICSEvent]) -> EventReplaySummary:
    event_list = list(events)
    sessions = {event.session_id for event in event_list}
    actors = {
        event.actor_id
        for event in event_list
        if event.actor_id is not None and event.actor_id.strip()
    }
    world_revisions = [
        event.world_revision
        for event in event_list
        if event.world_revision is not None
    ]

    return EventReplaySummary(
        total_events=len(event_list),
        sessions=len(sessions),
        actors=len(actors),
        protocols=dict(Counter(event.protocol for event in event_list)),
        intents=dict(Counter(event.intent.value for event in event_list)),
        results=dict(Counter(event.result for event in event_list)),
        first_timestamp=event_list[0].timestamp if event_list else None,
        last_timestamp=event_list[-1].timestamp if event_list else None,
        latest_world_revision=max(world_revisions) if world_revisions else None,
    )
