from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from agentic_plc.contracts.events import ICSEvent


class EventSink(Protocol):
    def append(self, event: ICSEvent) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class InMemoryEventLog:
    _events: list[ICSEvent] = field(default_factory=list)

    def append(self, event: ICSEvent) -> None:
        self._events.append(event)

    def list_events(self) -> list[ICSEvent]:
        return list(self._events)

    def iter_events(self) -> Iterator[ICSEvent]:
        yield from self._events

    def clear(self) -> None:
        self._events.clear()
