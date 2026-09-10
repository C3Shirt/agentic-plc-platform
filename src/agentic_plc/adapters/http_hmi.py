from __future__ import annotations

import json
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.telemetry.actors import ActorCorrelator, SessionContext
from agentic_plc.telemetry.event_log import EventSink
from agentic_plc.world.model import ControlAction, TankPumpWorld


@dataclass(frozen=True, slots=True)
class HMIServerRuntime:
    world: TankPumpWorld
    event_sink: EventSink | None = None
    actor_correlator: ActorCorrelator = field(default_factory=ActorCorrelator)


class TankPumpHMIHandler(BaseHTTPRequestHandler):
    server: "TankPumpHMIServer"

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(hmi_page(self.server.runtime.world.snapshot()))
            return
        if self.path == "/api/state":
            self._send_json(
                {
                    "scenario": "tank_pump_v1",
                    "state": self.server.runtime.world.snapshot(),
                }
            )
            self._record_event(
                intent=Intent.READ_PROCESS,
                operation="hmi_read_state",
                result="observed",
            )
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/api/control":
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            body = self._read_json_body()
            action = ControlAction(str(body["action"]))
            value = body.get("value")
            before = self.server.runtime.world.snapshot()
            self.server.runtime.world.apply_control(action, value)
            after = self.server.runtime.world.snapshot()
        except Exception as exc:
            self._record_event(
                intent=Intent.CONTROL_OUTPUT,
                operation="hmi_control",
                requested_value=_safe_body_for_event(locals().get("body")),
                result="rejected",
                metadata={"error": str(exc)},
            )
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self._record_event(
            intent=_intent_for_control(action),
            operation="hmi_control",
            requested_value={"action": action.value, "value": value},
            resulting_value={"revision": after["revision"]},
            result="accepted",
            world_revision=int(after["revision"]),
            metadata={"before_revision": before["revision"]},
        )
        self._send_json({"state": after})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _send_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(
        self,
        body: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _record_event(
        self,
        intent: Intent,
        operation: str,
        result: str,
        requested_value: Any = None,
        resulting_value: Any = None,
        world_revision: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_sink = self.server.runtime.event_sink
        if event_sink is None:
            return
        session_id = f"hmi:{self.client_address[0]}:{self.client_address[1]}"
        session_context = SessionContext(
            protocol="http_hmi",
            session_id=session_id,
            source_ip=self.client_address[0],
            source_port=self.client_address[1],
            destination_ip=self.server.server_address[0],
            destination_port=self.server.server_address[1],
        )
        event_sink.append(
            ICSEvent(
                protocol="http_hmi",
                session_id=session_id,
                source_ip=self.client_address[0],
                source_port=self.client_address[1],
                actor_id=self.server.runtime.actor_correlator.actor_id_for(
                    session_context
                ),
                intent=intent,
                operation=operation,
                requested_value=requested_value,
                resulting_value=resulting_value,
                result=result,
                world_revision=world_revision
                if world_revision is not None
                else self.server.runtime.world.state.revision,
                metadata=metadata or {},
            )
        )


class TankPumpHMIServer(ThreadingHTTPServer):
    def __init__(self, server_address, runtime: HMIServerRuntime) -> None:
        self.runtime = runtime
        super().__init__(server_address, TankPumpHMIHandler)


def create_tank_pump_hmi_server(
    host: str,
    port: int,
    world: TankPumpWorld,
    event_sink: EventSink | None = None,
) -> TankPumpHMIServer:
    return TankPumpHMIServer(
        (host, port),
        HMIServerRuntime(world=world, event_sink=event_sink),
    )


def hmi_page(snapshot: dict[str, object]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Tank Pump HMI</title>
</head>
<body>
  <h1>Tank Pump HMI</h1>
  <dl>
    <dt>Revision</dt><dd>{snapshot["revision"]}</dd>
    <dt>Mode</dt><dd>{snapshot["mode"]}</dd>
    <dt>Level</dt><dd>{snapshot["level_percent"]}%</dd>
    <dt>Pressure</dt><dd>{snapshot["pressure_bar"]} bar</dd>
    <dt>Setpoint</dt><dd>{snapshot["level_setpoint_percent"]}%</dd>
    <dt>High-level alarm</dt><dd>{snapshot["high_level_alarm"]}</dd>
  </dl>
</body>
</html>
"""


def _intent_for_control(action: ControlAction) -> Intent:
    if action is ControlAction.SET_LEVEL_SETPOINT:
        return Intent.WRITE_SETPOINT
    return Intent.CONTROL_OUTPUT


def _safe_body_for_event(body: object) -> object:
    if isinstance(body, dict):
        return dict(body)
    return None
