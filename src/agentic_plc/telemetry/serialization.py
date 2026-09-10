from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from agentic_plc.contracts.events import ICSEvent, Intent


_ICS_EVENT_FIELDS = {field.name for field in fields(ICSEvent)}


def event_to_dict(event: ICSEvent) -> dict[str, Any]:
    data = asdict(event)
    data["intent"] = event.intent.value
    return data


def event_from_dict(data: dict[str, Any]) -> ICSEvent:
    payload = {key: value for key, value in data.items() if key in _ICS_EVENT_FIELDS}
    payload["intent"] = Intent(payload["intent"])
    return ICSEvent(**payload)
