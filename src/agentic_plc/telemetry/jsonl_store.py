from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from agentic_plc.contracts.events import ICSEvent
from agentic_plc.telemetry.serialization import event_from_dict, event_to_dict


@dataclass(slots=True)
class JsonlEventStore:
    path: Path | str

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def append(self, event: ICSEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event_to_dict(event), sort_keys=True) + "\n")

    def append_many(self, events: Iterable[ICSEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event_to_dict(event), sort_keys=True) + "\n")

    def iter_events(self) -> Iterable[ICSEvent]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid event JSON at {self.path}:{line_number}"
                    ) from exc
                yield event_from_dict(payload)

    def list_events(self) -> list[ICSEvent]:
        return list(self.iter_events())

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
