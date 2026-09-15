from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from agentic_plc.agent.protocol_state_machine import ProtocolTransitionStatus
from agentic_plc.evaluation.consistency_benchmark import BenchmarkCase, BenchmarkStep
from agentic_plc.evaluation.physical_invariants import ProcessInvariant
from agentic_plc.telemetry.serialization import event_from_dict


def benchmark_step_from_dict(data: Mapping[str, Any]) -> BenchmarkStep:
    """Rebuild a benchmark step from the public JSON schema."""

    status_value = _required(
        data,
        "expected_protocol_status",
        aliases=("protocol_status",),
    )
    event_payload = _required(data, "event")
    if not isinstance(event_payload, Mapping):
        raise ValueError("benchmark step event must be an object")
    return BenchmarkStep(
        step_id=str(_required(data, "step_id")),
        request_hex=_optional_str(data.get("request_hex")),
        event=event_from_dict(dict(event_payload)),
        expected_protocol_status=ProtocolTransitionStatus(str(status_value)),
        expected_reply_generated=_optional_bool(
            data.get("expected_reply_generated")
        ),
        expected_world_patch_count=_optional_int(
            data.get("expected_world_patch_count")
        ),
        expected_process_values=_float_mapping(
            data.get("expected_process_values", {})
        ),
        expected_reply_values=_optional_int_tuple(data.get("expected_reply_values")),
        expected_response_kind=_optional_str(data.get("expected_response_kind")),
        expected_exception_code=_optional_int(data.get("expected_exception_code")),
        process_invariants=tuple(
            ProcessInvariant.from_dict(invariant)
            for invariant in data.get("process_invariants", ())
            if isinstance(invariant, Mapping)
        ),
        expected_snapshot_revision_delta=_optional_int(
            data.get("expected_snapshot_revision_delta")
        ),
        expected_patch_base_revision_matches_before=_optional_bool(
            data.get("expected_patch_base_revision_matches_before")
        ),
        expected_context_selected_variables=_optional_str_tuple(
            data.get("expected_context_selected_variables")
        ),
        expected_actor_memory_event_count=_optional_int(
            data.get("expected_actor_memory_event_count")
        ),
        expected_actor_memory_touched_variables=_optional_str_tuple(
            data.get("expected_actor_memory_touched_variables")
        ),
        tick_seconds_before=float(data.get("tick_seconds_before", 0.0)),
        tick_seconds_after=float(data.get("tick_seconds_after", 0.0)),
        forced_reply_hex=_optional_str(data.get("forced_reply_hex")),
        expected_forced_reply_accepted=_optional_bool(
            data.get("expected_forced_reply_accepted")
        ),
        notes=str(data.get("notes", "")),
    )


def benchmark_case_from_dict(data: Mapping[str, Any]) -> BenchmarkCase:
    """Rebuild one benchmark case from the public JSON schema."""

    steps_payload = _required(data, "steps")
    if not isinstance(steps_payload, list):
        raise ValueError("benchmark case steps must be a list")
    return BenchmarkCase(
        case_id=str(_required(data, "case_id")),
        description=str(data.get("description", "")),
        requires_process_context=bool(data.get("requires_process_context", True)),
        tags=tuple(str(tag) for tag in data.get("tags", ())),
        steps=tuple(
            benchmark_step_from_dict(step)
            for step in steps_payload
            if isinstance(step, Mapping)
        ),
    )


def benchmark_cases_from_payload(payload: object) -> tuple[BenchmarkCase, ...]:
    """Load benchmark cases from a JSON-compatible object.

    Accepted shapes:

    - `{"cases": [case, ...]}`: canonical artifact emitted by our tools.
    - `[case, ...]`: compact list form for hand-authored fixtures.
    - `{case}`: one direct benchmark case object.
    """

    if isinstance(payload, Mapping):
        cases_payload = payload.get("cases")
        if cases_payload is None and "case_id" in payload:
            return (benchmark_case_from_dict(payload),)
        if not isinstance(cases_payload, list):
            raise ValueError("benchmark JSON must contain a cases list")
        return tuple(
            benchmark_case_from_dict(case)
            for case in cases_payload
            if isinstance(case, Mapping)
        )
    if isinstance(payload, list):
        return tuple(
            benchmark_case_from_dict(case)
            for case in payload
            if isinstance(case, Mapping)
        )
    raise ValueError("benchmark JSON must be an object or a list")


def load_benchmark_cases(path: Path | str) -> tuple[BenchmarkCase, ...]:
    """Read benchmark cases from a JSON file."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return benchmark_cases_from_payload(payload)


def benchmark_cases_to_payload(cases: Iterable[BenchmarkCase]) -> dict[str, object]:
    """Serialize benchmark cases using the canonical tool output shape."""

    return {"cases": [case.to_dict() for case in cases]}


def write_benchmark_payload(
    path: Path | str,
    payload: Mapping[str, object],
) -> None:
    """Write a benchmark JSON artifact with stable formatting."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _required(
    data: Mapping[str, Any],
    key: str,
    *,
    aliases: tuple[str, ...] = (),
) -> Any:
    for candidate in (key, *aliases):
        if candidate in data and data[candidate] is not None:
            return data[candidate]
    raise ValueError(f"missing required benchmark field: {key}")


def _optional_str(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    raise ValueError(f"invalid optional boolean value: {value}")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_int_tuple(value: object) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("expected_reply_values must be a list or null")
    return tuple(int(item) for item in value)


def _optional_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("expected string tuple fields must be lists")
    return tuple(str(item) for item in value)


def _float_mapping(value: object) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("expected_process_values must be an object")
    return {str(key): float(item) for key, item in value.items()}
