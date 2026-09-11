from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from agentic_plc.agent.config import LLMConfig
from agentic_plc.agent.context_compressor import ContextBudget, ProcessContextCompressor
from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.contracts.actions import (
    AgentProposal,
    ProtocolReply,
    WorldPatch,
    WorldPatchOperation,
)
from agentic_plc.contracts.events import DeceptionPlan, ICSEvent, Intent
from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    build_modbus_tcp_read_bits_response,
    build_modbus_tcp_read_registers_response,
    build_modbus_tcp_response_from_request,
    build_modbus_tcp_write_multiple_response,
    build_modbus_tcp_write_single_response,
)
from agentic_plc.telemetry.serialization import event_to_dict


class PlannerResponseError(RuntimeError):
    """Raised when an LLM planner returns an unusable plan."""


class DeceptionPlanner(Protocol):
    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal | DeceptionPlan | None:
        raise NotImplementedError


class RuleBasedDeceptionPlanner:
    """No-LLM baseline planner for tests, fallback, and offline operation."""

    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal | None:
        if not events:
            return None

        actor_id = self._actor_id(events)
        intents = [event.intent for event in events]
        latest = events[-1]

        if latest.intent is Intent.READ_PROCESS and latest.transaction_id:
            unit_id = latest.unit_id if latest.unit_id is not None else 1
            function_code = int(latest.metadata.get("function_code", 3))
            count = int(latest.count or 2)
            values = self._process_values_for_read(context, latest)
            if function_code in {1, 2}:
                if values is None:
                    return None
                payload_hex = build_modbus_tcp_read_bits_response(
                    transaction_id=int(str(latest.transaction_id), 0),
                    unit_id=unit_id,
                    function_code=function_code,
                    values=values[:count],
                )
            elif function_code in {3, 4}:
                if values is None:
                    values = ([500, 120] + [0] * max(0, count - 2))[:count]
                payload_hex = build_modbus_tcp_read_registers_response(
                    transaction_id=int(str(latest.transaction_id), 0),
                    unit_id=unit_id,
                    function_code=function_code,
                    values=values,
                )
            else:
                return None
            return AgentProposal(
                protocol_reply=ProtocolReply(
                    protocol="modbus_tcp",
                    transaction_id=str(latest.transaction_id),
                    unit_id=unit_id,
                    payload_hex=payload_hex,
                    reason="Return a plausible generated Modbus process snapshot.",
                )
            )

        if latest.intent in {Intent.WRITE_SETPOINT, Intent.CONTROL_OUTPUT}:
            write_proposal = self._process_write_proposal(context, latest, actor_id)
            if write_proposal is not None:
                return write_proposal

        if Intent.FILE_TRANSFER in intents:
            return AgentProposal(
                deception_plan=DeceptionPlan(
                    actor_id=actor_id,
                    action="expose_existing_artifact",
                    target="logic_backups/tank_pump_v1.st",
                    reason="Actor attempted file transfer; expose a plausible PLC logic backup.",
                    ttl_seconds=900,
                )
            )

        if intents.count(Intent.WRITE_SETPOINT) >= 2:
            return AgentProposal(
                deception_plan=DeceptionPlan(
                    actor_id=actor_id,
                    action="publish_maintenance_note",
                    target="WO-2048",
                    reason="Repeated setpoint writes suggest process-tuning interest.",
                    ttl_seconds=900,
                    parameters={"topic": "level loop tuning"},
                ),
                world_patch=WorldPatch(
                    actor_id=actor_id,
                    reason="Show a plausible delayed process response after repeated setpoint tuning.",
                    ttl_seconds=120,
                    operations=[
                        WorldPatchOperation(
                            path="level_percent",
                            value=58.5,
                            reason="Keep the process close enough to the new setpoint to sustain interaction.",
                        )
                    ],
                ),
            )

        probing_count = intents.count(Intent.INVALID_ADDRESS) + intents.count(
            Intent.UNSUPPORTED_OPERATION
        )
        if probing_count >= 3:
            return AgentProposal(
                deception_plan=DeceptionPlan(
                    actor_id=actor_id,
                    action="expose_existing_artifact",
                    target="docs/register_map_excerpt.txt",
                    reason="Repeated probing indicates register-map discovery behavior.",
                    ttl_seconds=600,
                )
            )

        if Intent.AUTH_ATTEMPT in intents:
            return AgentProposal(
                deception_plan=DeceptionPlan(
                    actor_id=actor_id,
                    action="adjust_noncritical_narrative",
                    target="maintenance_gateway_banner",
                    reason="Authentication activity suggests interest in maintenance access.",
                    ttl_seconds=300,
                )
            )

        return None

    def _actor_id(self, events: list[ICSEvent]) -> str:
        for event in reversed(events):
            if event.actor_id:
                return event.actor_id
        return f"ip:{events[-1].source_ip}"

    def _process_values_for_read(
        self,
        context: PhysicalProcessContext | None,
        event: ICSEvent,
    ) -> list[int] | None:
        if context is None:
            return None
        try:
            return context.values_for_modbus_event(event)
        except ValueError:
            return None

    def _process_write_proposal(
        self,
        context: PhysicalProcessContext | None,
        event: ICSEvent,
        actor_id: str,
    ) -> AgentProposal | None:
        if context is None or event.transaction_id is None:
            return None
        try:
            world_patch = context.world_patch_for_modbus_write_event(event)
        except ValueError:
            return None
        if world_patch is None:
            return None
        payload_hex = self._modbus_write_ack_payload(event)
        if payload_hex is None:
            return None
        return AgentProposal(
            protocol_reply=ProtocolReply(
                protocol="modbus_tcp",
                transaction_id=str(event.transaction_id),
                unit_id=event.unit_id if event.unit_id is not None else 1,
                payload_hex=payload_hex,
                reason=(
                    "Acknowledge a mapped Modbus write after preparing the "
                    "corresponding process-state patch."
                ),
                metadata={"requires_accepted_world_patch": True},
            ),
            world_patch=WorldPatch(
                actor_id=actor_id,
                reason=world_patch.reason,
                ttl_seconds=world_patch.ttl_seconds,
                operations=world_patch.operations,
                metadata=world_patch.metadata,
            ),
        )

    def _modbus_write_ack_payload(self, event: ICSEvent) -> str | None:
        request_hex = event.metadata.get("request_hex")
        if isinstance(request_hex, str) and request_hex.strip():
            try:
                return build_modbus_tcp_response_from_request(request_hex)
            except ModbusFrameError:
                return None

        function_code = event.metadata.get("function_code")
        if function_code is None:
            return None
        if event.address is None:
            return None
        unit_id = event.unit_id if event.unit_id is not None else 1
        try:
            transaction_id = int(str(event.transaction_id), 0)
            function_code = int(function_code)
            if function_code in {5, 6}:
                values = _write_values_for_event(event)
                if not values:
                    return None
                return build_modbus_tcp_write_single_response(
                    transaction_id=transaction_id,
                    unit_id=unit_id,
                    function_code=function_code,
                    address=event.address,
                    value=values[0],
                )
            if function_code in {15, 16} and event.count is not None:
                return build_modbus_tcp_write_multiple_response(
                    transaction_id=transaction_id,
                    unit_id=unit_id,
                    function_code=function_code,
                    address=event.address,
                    count=event.count,
                )
        except (ValueError, ModbusFrameError):
            return None
        return None


class OpenAICompatiblePlanner:
    """Planner for OpenAI-compatible chat-completions APIs."""

    ALLOWED_ACTIONS = (
        "expose_existing_artifact",
        "publish_maintenance_note",
        "select_existing_fault_scenario",
        "adjust_noncritical_narrative",
    )

    def __init__(self, config: LLMConfig) -> None:
        if not config.is_configured:
            raise ValueError("LLM planner requires base_url and api_key")
        self._config = config
        self._context_compressor = ProcessContextCompressor(
            ContextBudget(
                max_points=config.context_max_points,
                max_events=config.context_max_events,
            )
        )

    def propose(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> AgentProposal | None:
        if not events:
            return None

        payload = {
            "model": self._config.model,
            "temperature": 0.1,
            "max_tokens": self._config.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(),
                },
                {
                    "role": "user",
                    "content": self._user_prompt(events, context),
                },
            ],
        }
        response = self._post_json(payload)
        content = response["choices"][0]["message"]["content"]
        plan_payload = parse_json_object(content)
        if plan_payload.get("action") in {None, "", "none"} and not any(
            key in plan_payload
            for key in ("deception_plan", "protocol_reply", "protocol_response", "world_patch")
        ):
            return None
        return proposal_from_payload(plan_payload)

    def _post_json(self, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for url in completion_url_candidates(self._config.base_url or ""):
            request = urllib.request.Request(
                url=url,
                data=body,
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self._config.timeout_seconds
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 404:
                    continue
                raise PlannerResponseError(
                    f"LLM planner request failed: HTTP Error {exc.code}: "
                    f"{_safe_http_error_body(exc)}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                break
        if isinstance(last_error, urllib.error.HTTPError):
            raise PlannerResponseError(
                f"LLM planner request failed: HTTP Error {last_error.code}: "
                f"{_safe_http_error_body(last_error)}"
            )
        raise PlannerResponseError(f"LLM planner request failed: {last_error}")

    def _system_prompt(self) -> str:
        return "Return only compact valid JSON. No markdown. No prose."

    def _user_prompt(
        self,
        events: list[ICSEvent],
        context: PhysicalProcessContext | None = None,
    ) -> str:
        compact_events = [self._compact_event(event) for event in events[-8:]]
        context_payload = (
            self._context_compressor.compress(context, events).to_prompt_dict()
            if context
            else None
        )
        writable_paths = (
            context.writable_variable_ids()
            if context
            else [
                "level_percent",
                "pressure_bar",
                "level_setpoint_percent",
                "inlet_valve_open",
                "outlet_pump_running",
                "high_level_alarm",
                "mode",
            ]
        )
        return (
            "You are a generic process-aware controller for an ICS honeypot. "
            "Infer the active physical process from process_context. Return one "
            "JSON object with keys deception_plan, protocol_reply, world_patch. "
            "Use null for unused keys. Base protocol replies on the current "
            "snapshot and exposed PLC points. For Modbus read_process events, "
            "protocol_reply must use protocol=modbus_tcp and include payload_hex, "
            "reason, transaction_id, unit_id. World patches may only use path "
            "values from allowed_world_patch_paths. For a successful Modbus "
            "write acknowledgement, include a world_patch that decodes the "
            "requested register value into the exact mapped process variable "
            "engineering value. Do not invent unmapped PLC addresses. Payload: "
            + json.dumps(
                {
                    "allowed_world_patch_paths": writable_paths,
                    "events": compact_events,
                    "process_context": context_payload,
                },
                sort_keys=True,
            )
        )

    def _compact_event(self, event: ICSEvent) -> dict[str, object]:
        data = event_to_dict(event)
        keep = {
            "protocol",
            "session_id",
            "source_ip",
            "actor_id",
            "intent",
            "operation",
            "address",
            "count",
            "requested_value",
            "result",
            "world_revision",
        }
        compact = {key: data[key] for key in keep if key in data}
        metadata = {
            key: event.metadata[key]
            for key in (
                "function_code",
                "exception_code",
                "interaction_phase",
                "interaction_phase_reason",
                "actor_protocol_event_count",
                "actor_protocol_unique_touched_addresses",
                "actor_protocol_last_write_range",
            )
            if key in event.metadata
        }
        if metadata:
            compact["metadata"] = metadata
        return compact


def completion_url(base_url: str) -> str:
    return completion_url_candidates(base_url)[0]


def completion_url_candidates(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return [base]
    candidates = [f"{base}/chat/completions"]
    parsed = urllib.parse.urlparse(base)
    path = parsed.path.rstrip("/")
    if path != "/v1":
        candidates.append(f"{base}/v1/chat/completions")
    return list(dict.fromkeys(candidates))


def _safe_http_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:
        return "<unreadable>"
    compact = " ".join(body.split())
    return compact[:500] if compact else "<empty>"


def parse_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise PlannerResponseError("LLM planner did not return JSON") from exc
    if not isinstance(payload, dict):
        raise PlannerResponseError("LLM planner JSON must be an object")
    return payload


def proposal_from_payload(payload: dict[str, object]) -> AgentProposal | None:
    deception_payload = payload.get("deception_plan")
    if deception_payload is None and payload.get("action") not in {None, "", "none"}:
        deception_payload = payload

    protocol_payload = payload.get("protocol_reply")
    if protocol_payload is None:
        protocol_payload = payload.get("protocol_response")

    world_payload = payload.get("world_patch")

    deception_plan = (
        _parse_deception_plan(deception_payload)
        if isinstance(deception_payload, dict)
        else None
    )
    protocol_reply = (
        _parse_protocol_reply(protocol_payload)
        if isinstance(protocol_payload, dict)
        else None
    )
    world_patch = (
        _parse_world_patch(world_payload) if isinstance(world_payload, dict) else None
    )

    if deception_plan is None and protocol_reply is None and world_patch is None:
        return None
    return AgentProposal(
        deception_plan=deception_plan,
        protocol_reply=protocol_reply,
        world_patch=world_patch,
    )


def _parse_deception_plan(payload: dict[str, object]) -> DeceptionPlan | None:
    if payload.get("action") in {None, "", "none"}:
        return None
    return DeceptionPlan(
        actor_id=str(payload["actor_id"]),
        action=str(payload["action"]),
        target=str(payload["target"]),
        reason=str(payload["reason"]),
        ttl_seconds=int(payload.get("ttl_seconds", 300)),
        parameters=dict(payload.get("parameters", {})),
    )


def _parse_protocol_reply(payload: dict[str, object]) -> ProtocolReply | None:
    payload_hex = payload.get("payload_hex")
    if payload_hex in {None, "", "none"}:
        return None
    return ProtocolReply(
        protocol=str(payload.get("protocol", "modbus_tcp")),
        payload_hex=str(payload_hex),
        reason=str(payload["reason"]),
        transaction_id=(
            None
            if payload.get("transaction_id") is None
            else str(payload.get("transaction_id"))
        ),
        unit_id=None if payload.get("unit_id") is None else int(payload["unit_id"]),
        metadata=dict(payload.get("metadata", {})),
    )


def _parse_world_patch(payload: dict[str, object]) -> WorldPatch | None:
    operations_payload = payload.get("operations")
    if not operations_payload:
        return None
    if not isinstance(operations_payload, list):
        raise PlannerResponseError("world_patch.operations must be a list")
    return WorldPatch(
        actor_id=str(payload["actor_id"]),
        reason=str(payload["reason"]),
        ttl_seconds=int(payload.get("ttl_seconds", 300)),
        operations=[
            WorldPatchOperation(
                path=str(operation["path"]),
                value=operation.get("value"),
                reason=str(operation.get("reason", "")),
            )
            for operation in operations_payload
            if isinstance(operation, dict)
        ],
        metadata=dict(payload.get("metadata", {})),
    )


def _write_values_for_event(event: ICSEvent) -> list[object] | None:
    if event.requested_value is None:
        return None
    if isinstance(event.requested_value, list):
        return list(event.requested_value)
    if isinstance(event.requested_value, tuple):
        return list(event.requested_value)
    return [event.requested_value]
