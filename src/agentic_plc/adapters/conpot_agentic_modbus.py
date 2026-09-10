from __future__ import annotations

import codecs
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from agentic_plc.agent.runtime import AgentRuntime
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    ModbusTcpRequest,
    parse_modbus_tcp_frame,
    parse_modbus_tcp_request,
)
from agentic_plc.telemetry.actors import ActorCorrelator, SessionContext


class ConpotDatabankLike(Protocol):
    def handle_request(self, query: Any, request: bytes, mode: str) -> tuple[Any, dict]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ModbusHookContext:
    session_id: str = "conpot-agentic-modbus"
    source_ip: str = "0.0.0.0"
    actor_id: str | None = None
    source_port: int | None = None
    destination_ip: str | None = None
    destination_port: int | None = None


class AgenticModbusDatabank:
    """Wraps a Conpot Modbus databank with validated agent-generated replies."""

    def __init__(
        self,
        inner: ConpotDatabankLike,
        runtime: AgentRuntime,
        context: ModbusHookContext | None = None,
        actor_correlator: ActorCorrelator | None = None,
    ) -> None:
        self._inner = inner
        self._runtime = runtime
        self._context = context or ModbusHookContext()
        self._actor_correlator = actor_correlator or ActorCorrelator()
        self.last_agent_error: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def handle_request(
        self,
        query: Any,
        request: bytes,
        mode: str,
        context: Mapping[str, Any] | ModbusHookContext | None = None,
    ) -> tuple[Any, dict]:
        self.last_agent_error = None
        try:
            parsed_request = parse_modbus_tcp_request(request)
            hook_context = self._resolve_context(context)
            event = event_from_modbus_tcp_request(
                parsed_request,
                context=hook_context,
                actor_correlator=self._actor_correlator,
            )
            decision = self._runtime.observe_and_decide(event)
            if decision.protocol_replies:
                reply = decision.protocol_replies[0]
                frame = parse_modbus_tcp_frame(reply.payload_hex)
                response = frame.raw
                return response, {
                    "request": codecs.encode(request[7:], "hex"),
                    "slave_id": frame.unit_id,
                    "function_code": (
                        frame.function_code & 0x7F
                        if frame.is_exception
                        else frame.function_code
                    ),
                    "response": codecs.encode(frame.raw[7:], "hex"),
                    "agentic_generated": True,
                    "agentic_reason": reply.reason,
                }
        except Exception as exc:
            self.last_agent_error = str(exc)

        response, logdata = self._inner.handle_request(query, request, mode)
        if self.last_agent_error:
            logdata = dict(logdata)
            logdata["agentic_error"] = self.last_agent_error
        return response, logdata

    def _resolve_context(
        self,
        context: Mapping[str, Any] | ModbusHookContext | None,
    ) -> ModbusHookContext:
        if context is None:
            return self._context
        if isinstance(context, ModbusHookContext):
            return context
        merged = self._context
        for field_name in (
            "session_id",
            "source_ip",
            "actor_id",
            "source_port",
            "destination_ip",
            "destination_port",
        ):
            if field_name in context and context[field_name] is not None:
                normalized = _normalize_context_value(
                    field_name,
                    context[field_name],
                )
                merged = replace(
                    merged,
                    **{field_name: normalized},
                )
        return merged


def install_agentic_modbus_hook(
    server: Any,
    runtime: AgentRuntime,
    context: ModbusHookContext | None = None,
) -> AgenticModbusDatabank:
    """Install an agent hook on an initialized Conpot ModbusServer instance."""

    target = getattr(server, "wrapped", server)
    databank = getattr(target, "_databank")
    wrapped = AgenticModbusDatabank(databank, runtime, context=context)
    if hasattr(target, "set_request_hook"):
        target.set_request_hook(
            lambda query, request, mode, context: wrapped.handle_request(
                query=query,
                request=request,
                mode=mode,
                context=context,
            )
        )
    else:
        setattr(target, "_databank", wrapped)
    return wrapped


def event_from_modbus_tcp_request(
    request: bytes | ModbusTcpRequest,
    context: ModbusHookContext | None = None,
    actor_correlator: ActorCorrelator | None = None,
) -> ICSEvent:
    context = context or ModbusHookContext()
    session_context = SessionContext(
        protocol="modbus",
        session_id=context.session_id,
        source_ip=context.source_ip,
        source_port=context.source_port,
        destination_ip=context.destination_ip,
        destination_port=context.destination_port,
    )
    actor_correlator = actor_correlator or ActorCorrelator()
    parsed = (
        request if isinstance(request, ModbusTcpRequest) else parse_modbus_tcp_request(request)
    )
    requested_value: object | None
    if not parsed.values:
        requested_value = None
    elif len(parsed.values) == 1:
        requested_value = parsed.values[0]
    else:
        requested_value = list(parsed.values)

    return ICSEvent(
        protocol="modbus",
        session_id=context.session_id,
        source_ip=context.source_ip,
        actor_id=context.actor_id or actor_correlator.actor_id_for(session_context),
        source_port=context.source_port,
        transaction_id=str(parsed.transaction_id),
        unit_id=parsed.unit_id,
        intent=_intent_for_request(parsed),
        operation=_operation_for_function(parsed.function_code),
        address=parsed.address,
        count=parsed.count,
        requested_value=requested_value,
        result="observed",
        metadata={
            "function_code": parsed.function_code,
            "request_hex": parsed.raw.hex(),
        },
    )


def _intent_for_request(request: ModbusTcpRequest) -> Intent:
    if request.function_code in {1, 2, 3, 4}:
        return Intent.READ_PROCESS
    if request.function_code in {5, 15}:
        return Intent.CONTROL_OUTPUT
    if request.function_code in {6, 16} and request.address == 0:
        return Intent.WRITE_SETPOINT
    if request.function_code in {6, 16}:
        return Intent.CONTROL_OUTPUT
    raise ModbusFrameError(f"unsupported Modbus function: {request.function_code}")


def _operation_for_function(function_code: int) -> str:
    return {
        1: "read_coils",
        2: "read_discrete_inputs",
        3: "read_holding_registers",
        4: "read_input_registers",
        5: "write_single_coil",
        6: "write_single_register",
        15: "write_multiple_coils",
        16: "write_multiple_registers",
    }.get(function_code, "unsupported")


def _normalize_context_value(field_name: str, value: Any) -> str | int:
    if field_name in {"source_port", "destination_port"}:
        return int(value)
    return str(value)
