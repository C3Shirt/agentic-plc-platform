from __future__ import annotations

import json
import socketserver
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentic_plc.evaluation import (
    BenchmarkCase,
    HMIStateObserver,
    LiveModbusBenchmarkRunner,
    build_default_modbus_consistency_cases,
    decode_modbus_response_values,
    send_modbus_tcp_request,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_exception_response,
    build_modbus_tcp_response_from_request,
    parse_modbus_tcp_frame,
    parse_modbus_tcp_request,
)


class LiveModbusBenchmarkTests(unittest.TestCase):
    def test_send_request_and_decode_live_modbus_response(self) -> None:
        state = _ProcessState()
        modbus_server = _start_modbus_server(state)
        try:
            step = build_default_modbus_consistency_cases()[0].steps[0]
            response_hex = send_modbus_tcp_request(
                "127.0.0.1",
                modbus_server.server_address[1],
                step.request_hex or "",
            )

            frame = parse_modbus_tcp_frame(response_hex)

            self.assertEqual(frame.transaction_id, 100)
            self.assertEqual(decode_modbus_response_values(frame), (500,))
        finally:
            _stop_server(modbus_server)

    def test_runner_checks_hmi_observed_write_then_readback(self) -> None:
        state = _ProcessState()
        modbus_server = _start_modbus_server(state)
        hmi_server = _start_hmi_server(state)
        try:
            case = _default_case("modbus_write_then_readback")

            report = LiveModbusBenchmarkRunner(
                host="127.0.0.1",
                port=modbus_server.server_address[1],
                hmi_observer=HMIStateObserver(
                    f"http://127.0.0.1:{hmi_server.server_address[1]}/api/state"
                ),
            ).run((case,))

            results = report.cases["modbus_write_then_readback"]
            self.assertTrue(report.passed)
            self.assertEqual(results[0].process_values["level_sp"], 70.0)
            self.assertEqual(results[1].reply_values, (700,))
            self.assertTrue(
                results[1].checks[
                    "process_invariant:read_reply_matches_process_snapshot"
                ]
            )
        finally:
            _stop_server(modbus_server)
            _stop_server(hmi_server)

    def test_runner_skips_physical_checks_without_hmi_observer(self) -> None:
        state = _ProcessState()
        modbus_server = _start_modbus_server(state)
        try:
            case = _default_case("modbus_write_then_readback")

            report = LiveModbusBenchmarkRunner(
                host="127.0.0.1",
                port=modbus_server.server_address[1],
            ).run((case,))

            results = report.cases["modbus_write_then_readback"]
            self.assertTrue(report.passed)
            self.assertEqual(results[1].reply_values, (700,))
            self.assertNotIn(
                "process_invariant:read_reply_matches_process_snapshot",
                results[1].checks,
            )
        finally:
            _stop_server(modbus_server)

    def test_runner_can_check_protocol_status_from_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_log = Path(directory) / "events.jsonl"
            state = _ProcessState(event_log_path=event_log)
            modbus_server = _start_modbus_server(state)
            try:
                step = replace(
                    build_default_modbus_consistency_cases()[0].steps[0],
                    expected_reply_values=(500,),
                )
                case = BenchmarkCase(
                    case_id="live_read_with_event_log",
                    description="Live read plus JSONL protocol status check.",
                    steps=(step,),
                    requires_process_context=False,
                )

                report = LiveModbusBenchmarkRunner(
                    host="127.0.0.1",
                    port=modbus_server.server_address[1],
                    event_log_path=event_log,
                ).run((case,))

                result = report.cases["live_read_with_event_log"][0]
                self.assertTrue(report.passed)
                self.assertEqual(result.protocol_status, "allowed")
                self.assertTrue(result.checks["protocol_status"])
            finally:
                _stop_server(modbus_server)


class _ProcessState:
    def __init__(self, event_log_path: Path | None = None) -> None:
        self.level_setpoint_percent = 50.0
        self.event_log_path = event_log_path


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class _ModbusHandler(socketserver.BaseRequestHandler):
    server: "_ModbusServer"

    def handle(self) -> None:
        header = _recv_exact(self.request, 7)
        length = int.from_bytes(header[4:6], "big")
        request = header + _recv_exact(self.request, length - 1)
        parsed = parse_modbus_tcp_request(request)
        self.server.state.append_event(request.hex())

        if parsed.function_code == 3 and parsed.address == 0:
            values = [int(round(self.server.state.level_setpoint_percent * 10))]
            response_hex = build_modbus_tcp_response_from_request(parsed, values=values)
        elif parsed.function_code == 6 and parsed.address == 0 and parsed.values:
            self.server.state.level_setpoint_percent = parsed.values[0] / 10.0
            response_hex = build_modbus_tcp_response_from_request(parsed)
        else:
            response_hex = build_modbus_tcp_exception_response(
                transaction_id=parsed.transaction_id,
                unit_id=parsed.unit_id,
                function_code=parsed.function_code,
                exception_code=2,
            )
        self.request.sendall(bytes.fromhex(response_hex))


class _ModbusServer(_ReusableTCPServer):
    state: "_ServerState"


class _ServerState:
    def __init__(self, process_state: _ProcessState) -> None:
        self.process_state = process_state

    @property
    def level_setpoint_percent(self) -> float:
        return self.process_state.level_setpoint_percent

    @level_setpoint_percent.setter
    def level_setpoint_percent(self, value: float) -> None:
        self.process_state.level_setpoint_percent = value

    def append_event(self, request_hex: str) -> None:
        if self.process_state.event_log_path is None:
            return
        self.process_state.event_log_path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "request_hex": request_hex,
                        "protocol_fsm_status": "allowed",
                        "protocol_fsm_reason": "test_allowed",
                    }
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


class _HMIHandler(BaseHTTPRequestHandler):
    server: "_HMIServer"

    def do_GET(self) -> None:
        if self.path != "/api/state":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "scenario": "test_tank",
            "state": {
                "level_setpoint_percent": self.server.state.level_setpoint_percent,
                "level_percent": 48.0,
                "pressure_bar": 1.2,
                "mode": "auto",
                "outlet_pump_running": True,
                "inlet_valve_open": True,
                "high_level_alarm": False,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _HMIServer(ThreadingHTTPServer):
    state: _ProcessState


def _default_case(case_id: str) -> BenchmarkCase:
    return next(
        case
        for case in build_default_modbus_consistency_cases()
        if case.case_id == case_id
    )


def _start_modbus_server(process_state: _ProcessState) -> _ModbusServer:
    server = _ModbusServer(("127.0.0.1", 0), _ModbusHandler)
    server.state = _ServerState(process_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_hmi_server(process_state: _ProcessState) -> _HMIServer:
    server = _HMIServer(("127.0.0.1", 0), _HMIHandler)
    server.state = process_state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop_server(server: socketserver.BaseServer) -> None:
    server.shutdown()
    server.server_close()


def _recv_exact(sock, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


if __name__ == "__main__":
    unittest.main()
