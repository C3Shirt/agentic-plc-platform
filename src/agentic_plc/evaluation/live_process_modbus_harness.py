from __future__ import annotations

import json
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Protocol

from agentic_plc.adapters import ModbusHookContext, event_from_modbus_tcp_request
from agentic_plc.agent.protocol_state_machine import ProtocolStateMachineRegistry
from agentic_plc.processes.base import ProcessBackend, ProcessSnapshot
from agentic_plc.protocols.modbus import (
    ModbusFrameError,
    ModbusTcpRequest,
    build_modbus_tcp_response_from_request,
    parse_modbus_tcp_request,
)
from agentic_plc.telemetry.actors import ActorCorrelator
from agentic_plc.telemetry.serialization import event_to_dict
from agentic_plc.world.registers import RegisterAccessError, RegisterArea


class RegisterMapLike(Protocol):
    backend: ProcessBackend

    def read(self, area: RegisterArea | str, address: int, count: int = 1) -> list[int]:
        raise NotImplementedError

    def write(
        self,
        area: RegisterArea | str,
        address: int,
        value: int | bool,
    ) -> object:
        raise NotImplementedError

    def write_many(
        self,
        area: RegisterArea | str,
        address: int,
        values: tuple[int | bool, ...],
    ) -> list[object]:
        raise NotImplementedError


@dataclass(slots=True)
class ProcessMappedModbusRuntime:
    """Small Modbus TCP runtime backed by a generic ProcessRegisterMap.

    This harness is intentionally test/evaluation oriented. It does not replace
    Conpot; it gives benchmark tools a dependency-light live endpoint that can
    validate cyber-physical consistency for any scenario-backed process slice.
    """

    register_map: RegisterMapLike
    event_log_path: Path | None = None
    exception_code_for_access_error: int = 2
    state_machine: ProtocolStateMachineRegistry = field(
        default_factory=ProtocolStateMachineRegistry
    )
    actor_correlator: ActorCorrelator = field(default_factory=ActorCorrelator)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def handle_request(
        self,
        request: bytes,
        *,
        client_address: tuple[str, int],
        server_address: tuple[str, int],
    ) -> bytes:
        parsed = parse_modbus_tcp_request(request)
        self._record_event(
            parsed,
            client_address=client_address,
            server_address=server_address,
        )
        try:
            values = self._apply_or_read(parsed)
            response_hex = build_modbus_tcp_response_from_request(
                parsed,
                values=values,
            )
        except (KeyError, RegisterAccessError, ValueError):
            response_hex = build_modbus_tcp_response_from_request(
                parsed,
                exception_code=self.exception_code_for_access_error,
            )
        return bytes.fromhex(response_hex)

    def _apply_or_read(self, parsed: ModbusTcpRequest) -> tuple[int | bool, ...] | None:
        if parsed.address is None:
            raise RegisterAccessError("missing Modbus address")
        with self.lock:
            if parsed.function_code in {1, 2, 3, 4}:
                if parsed.count is None:
                    raise RegisterAccessError("missing Modbus read count")
                return tuple(
                    self.register_map.read(
                        _area_for_function(parsed.function_code),
                        parsed.address,
                        parsed.count,
                    )
                )
            if parsed.function_code == 5:
                self.register_map.write(
                    RegisterArea.COILS,
                    parsed.address,
                    parsed.values[0],
                )
                return None
            if parsed.function_code == 6:
                self.register_map.write(
                    RegisterArea.HOLDING_REGISTERS,
                    parsed.address,
                    parsed.values[0],
                )
                return None
            if parsed.function_code == 15:
                self.register_map.write_many(
                    RegisterArea.COILS,
                    parsed.address,
                    tuple(bool(value) for value in parsed.values),
                )
                return None
            if parsed.function_code == 16:
                self.register_map.write_many(
                    RegisterArea.HOLDING_REGISTERS,
                    parsed.address,
                    parsed.values,
                )
                return None
        raise ModbusFrameError(f"unsupported Modbus function: {parsed.function_code}")

    def _record_event(
        self,
        parsed: ModbusTcpRequest,
        *,
        client_address: tuple[str, int],
        server_address: tuple[str, int],
    ) -> None:
        if self.event_log_path is not None:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        context = ModbusHookContext(
            session_id=f"modbus:{client_address[0]}:{client_address[1]}",
            source_ip=client_address[0],
            source_port=client_address[1],
            destination_ip=server_address[0],
            destination_port=server_address[1],
        )
        event = event_from_modbus_tcp_request(
            parsed,
            context=context,
            actor_correlator=self.actor_correlator,
        )
        _, annotated = self.state_machine.observe(event)
        if self.event_log_path is None:
            return
        with self.lock:
            with self.event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event_to_dict(annotated), sort_keys=True))
                handle.write("\n")


@dataclass(frozen=True, slots=True)
class ServerHandle:
    host: str
    port: int
    server: socketserver.BaseServer
    thread: threading.Thread

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


class ProcessMappedModbusTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        runtime: ProcessMappedModbusRuntime,
    ) -> None:
        self.runtime = runtime
        super().__init__(server_address, ProcessMappedModbusHandler)


class ProcessMappedModbusHandler(socketserver.BaseRequestHandler):
    server: ProcessMappedModbusTcpServer
    request: socket.socket

    def handle(self) -> None:
        while True:
            try:
                header = _recv_exact(self.request, 7)
                length = int.from_bytes(header[4:6], "big")
                if length <= 0:
                    return
                body = _recv_exact(self.request, length - 1)
            except ConnectionError:
                return
            response = self.server.runtime.handle_request(
                header + body,
                client_address=self.client_address,
                server_address=self.server.server_address,
            )
            self.request.sendall(response)


@dataclass(frozen=True, slots=True)
class ProcessHmiRuntime:
    backend: ProcessBackend
    scenario_id: str


class ProcessHmiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        runtime: ProcessHmiRuntime,
    ) -> None:
        self.runtime = runtime
        super().__init__(server_address, ProcessHmiHandler)


class ProcessHmiHandler(BaseHTTPRequestHandler):
    server: ProcessHmiServer

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_json(self._state_payload())
            return
        if self.path == "/api/state":
            self._send_json(self._state_payload())
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _state_payload(self) -> dict[str, object]:
        snapshot = self.server.runtime.backend.snapshot()
        return {
            "scenario": self.server.runtime.scenario_id,
            "state": process_snapshot_to_hmi_state(snapshot),
        }

    def _send_json(
        self,
        payload: Mapping[str, object],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_process_mapped_modbus_server(
    register_map: RegisterMapLike,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    event_log_path: Path | str | None = None,
) -> ServerHandle:
    runtime = ProcessMappedModbusRuntime(
        register_map=register_map,
        event_log_path=Path(event_log_path) if event_log_path is not None else None,
    )
    server = ProcessMappedModbusTcpServer((host, int(port)), runtime)
    return _start_server(server)


def start_process_hmi_server(
    backend: ProcessBackend,
    *,
    scenario_id: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ServerHandle:
    server = ProcessHmiServer(
        (host, int(port)),
        ProcessHmiRuntime(
            backend=backend,
            scenario_id=scenario_id,
        ),
    )
    return _start_server(server)


def process_snapshot_to_hmi_state(snapshot: ProcessSnapshot) -> dict[str, object]:
    state: dict[str, object] = {
        "process_id": snapshot.process_id,
        "backend_name": snapshot.backend_name,
        "revision": snapshot.revision,
        "simulated_seconds": snapshot.simulated_seconds,
    }
    state.update({key: float(value) for key, value in snapshot.measurements.items()})
    state.update(
        {key: float(value) for key, value in snapshot.manipulated_variables.items()}
    )
    state.update({key: float(value) for key, value in snapshot.setpoints.items()})
    return state


def _start_server(server: socketserver.BaseServer) -> ServerHandle:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return ServerHandle(
        host=str(host),
        port=int(port),
        server=server,
        thread=thread,
    )


def _area_for_function(function_code: int) -> RegisterArea:
    return {
        1: RegisterArea.COILS,
        2: RegisterArea.DISCRETE_INPUTS,
        3: RegisterArea.HOLDING_REGISTERS,
        4: RegisterArea.INPUT_REGISTERS,
    }[function_code]


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed before full Modbus request")
        chunks.extend(chunk)
    return bytes(chunks)
