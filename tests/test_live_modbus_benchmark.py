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
    ModbusAttackGenerationOptions,
    ModbusTableSpec,
    benchmark_cases_from_payload,
    benchmark_cases_to_payload,
    build_generated_modbus_attack_cases,
    build_default_modbus_consistency_cases,
    build_default_live_modbus_cases,
    decode_modbus_response_values,
    hmi_register_bindings_from_scenario,
    modbus_attack_options_from_scenario,
    modbus_points_from_scenario,
    send_modbus_tcp_request,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_exception_response,
    build_modbus_tcp_response_from_request,
    parse_modbus_tcp_frame,
    parse_modbus_tcp_request,
)


class LiveModbusBenchmarkTests(unittest.TestCase):
    def test_default_live_cases_cover_expanded_modbus_behaviors(self) -> None:
        cases = build_default_live_modbus_cases()

        tags = {tag for case in cases for tag in case.tags}
        steps = [step for case in cases for step in case.steps]

        self.assertGreaterEqual(len(cases), 6)
        self.assertGreaterEqual(len(steps), 18)
        self.assertIn("multi_table", tags)
        self.assertIn("multiple_write", tags)
        self.assertIn("exception", tags)
        self.assertIn("anomaly", tags)
        self.assertIn("exception", {step.expected_response_kind for step in steps})

    def test_default_live_cases_round_trip_extended_expectations(self) -> None:
        loaded = benchmark_cases_from_payload(
            benchmark_cases_to_payload(build_default_live_modbus_cases())
        )

        exception_step = next(
            step
            for case in loaded
            if case.case_id == "live_modbus_exception_probing"
            for step in case.steps
            if step.step_id == "read_unmapped_holding_register"
        )

        self.assertEqual(exception_step.expected_response_kind, "exception")
        self.assertEqual(exception_step.expected_exception_code, 2)

    def test_generated_attack_cases_round_trip_and_are_deterministic(self) -> None:
        options = ModbusAttackGenerationOptions(
            seed=11,
            transaction_start=1000,
            scan_stop=4,
            setpoint_values=(600, 650),
            coil_values=(True,),
        )

        first = build_generated_modbus_attack_cases(options)
        second = build_generated_modbus_attack_cases(options)
        loaded = benchmark_cases_from_payload(benchmark_cases_to_payload(first))

        self.assertEqual(benchmark_cases_to_payload(first), benchmark_cases_to_payload(second))
        self.assertEqual(len(loaded), 6)
        self.assertEqual(sum(len(case.steps) for case in loaded), 35)
        transaction_case = next(
            case
            for case in loaded
            if case.case_id == "generated_modbus_transaction_abuse_seed_11"
        )
        self.assertEqual(
            transaction_case.steps[1].expected_protocol_status.value,
            "anomalous",
        )

    def test_generated_attack_options_can_override_process_bindings(self) -> None:
        options = ModbusAttackGenerationOptions(
            seed=13,
            transaction_start=1200,
            scan_stop=2,
            tables=(ModbusTableSpec("custom_holding", 3, 4),),
            setpoint_register_address=10,
            mode_register_address=11,
            pump_coil_address=20,
            inlet_coil_address=21,
            setpoint_variable="reactor_temp_sp",
            pump_variable="feed_pump_cmd",
            inlet_variable="purge_valve_open",
            encoded_setpoint_scale=0.01,
            setpoint_values=(1234,),
            coil_values=(False,),
            include_recon_sweep=False,
            include_exception_probing=False,
            include_transaction_abuse=False,
            include_multi_session_interleave=False,
        )

        cases = build_generated_modbus_attack_cases(options)
        write_case = next(
            case
            for case in cases
            if case.case_id == "generated_modbus_write_readback_seed_13"
        )
        batch_case = next(
            case
            for case in cases
            if case.case_id == "generated_modbus_batch_writes_seed_13"
        )

        setpoint_write = parse_modbus_tcp_request(write_case.steps[0].request_hex)
        setpoint_readback = parse_modbus_tcp_request(write_case.steps[1].request_hex)
        coil_write = parse_modbus_tcp_request(write_case.steps[2].request_hex)
        batch_register_write = parse_modbus_tcp_request(batch_case.steps[0].request_hex)
        batch_coil_write = parse_modbus_tcp_request(batch_case.steps[2].request_hex)

        self.assertEqual(setpoint_write.address, 10)
        self.assertEqual(setpoint_readback.address, 10)
        self.assertEqual(coil_write.address, 20)
        self.assertEqual(batch_register_write.address, 10)
        self.assertEqual(batch_coil_write.address, 20)
        self.assertEqual(
            write_case.steps[0].expected_process_values,
            {"reactor_temp_sp": 12.34},
        )
        self.assertEqual(
            batch_case.steps[2].expected_process_values,
            {"feed_pump_cmd": 0.0, "purge_valve_open": 1.0},
        )

    def test_generated_attack_options_can_be_loaded_from_te_scenario(self) -> None:
        scenario_path = Path("scenarios/tennessee_eastman/scenario.json")

        options = modbus_attack_options_from_scenario(
            scenario_path,
            seed=21,
            scan_stop=3,
        )
        cases = build_generated_modbus_attack_cases(options)
        bindings = hmi_register_bindings_from_scenario(scenario_path)

        self.assertEqual(options.profile, "tennessee_eastman_reactor_separator_cell")
        self.assertEqual(options.setpoint_register_address, 0)
        self.assertEqual(options.mode_register_address, 1)
        self.assertIsNone(options.pump_coil_address)
        self.assertEqual(options.setpoint_variable, "xset_08")
        self.assertFalse(options.include_setpoint_bounds_invariant)
        self.assertEqual(len(cases), 6)
        self.assertEqual(sum(len(case.steps) for case in cases), 22)
        self.assertFalse(
            any(
                "pump_coil" in step.step_id
                for case in cases
                for step in case.steps
            )
        )
        self.assertEqual(bindings[0].state_key, "xset_08")
        self.assertEqual(bindings[1].scale, 10.0)

    def test_scenario_driven_scan_uses_exact_sparse_addresses(self) -> None:
        scenario = {
            "scenario_id": "sparse_process",
            "points": [
                {
                    "variable_id": "reactor_level",
                    "protocol": "modbus",
                    "table": "input_registers",
                    "address": 10,
                    "access": "read",
                    "scale": 10.0,
                },
                {
                    "variable_id": "reactor_sp",
                    "protocol": "modbus",
                    "table": "holding_registers",
                    "address": 20,
                    "access": "read_write",
                    "scale": 10.0,
                },
            ],
        }

        options = modbus_attack_options_from_scenario(
            scenario,
            seed=31,
            scan_start=9,
            scan_stop=12,
            include_write_readback=False,
            include_batch_writes=False,
            include_exception_probing=False,
            include_transaction_abuse=False,
            include_multi_session_interleave=False,
        )
        cases = build_generated_modbus_attack_cases(options)
        scan_case = cases[0]
        status_by_address = {
            parse_modbus_tcp_request(step.request_hex).address: step.expected_response_kind
            for step in scan_case.steps
            if step.event.operation == "read_input_registers"
        }

        self.assertEqual(options.tables[0].mapped_addresses, (20,))
        self.assertEqual(options.tables[1].mapped_addresses, (10,))
        self.assertEqual(status_by_address, {9: "exception", 10: "normal", 11: "exception"})

    def test_legacy_tank_pump_register_map_can_be_loaded_as_scenario(self) -> None:
        points = modbus_points_from_scenario(Path("scenarios/tank_pump/scenario.json"))
        options = modbus_attack_options_from_scenario(
            Path("scenarios/tank_pump/scenario.json"),
            seed=41,
            scan_stop=2,
        )

        self.assertEqual(len(points), 7)
        self.assertEqual(options.profile, "tank_pump_v1")
        self.assertEqual(options.setpoint_register_address, 0)
        self.assertEqual(options.mode_register_address, 1)
        self.assertEqual(options.pump_coil_address, 0)
        self.assertEqual(options.inlet_coil_address, 1)
        self.assertEqual(options.encoded_setpoint_scale, 0.1)

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

    def test_runner_passes_expanded_default_live_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_log = Path(directory) / "events.jsonl"
            state = _ProcessState(event_log_path=event_log)
            modbus_server = _start_modbus_server(state)
            hmi_server = _start_hmi_server(state)
            try:
                report = LiveModbusBenchmarkRunner(
                    host="127.0.0.1",
                    port=modbus_server.server_address[1],
                    hmi_observer=HMIStateObserver(
                        f"http://127.0.0.1:{hmi_server.server_address[1]}/api/state"
                    ),
                    event_log_path=event_log,
                ).run(build_default_live_modbus_cases())

                self.assertTrue(report.passed)
                self.assertEqual(report.total_steps, 18)
                self.assertEqual(
                    report.status_counts(),
                    {"allowed": 17, "anomalous": 1},
                )
                exception_result = report.cases["live_modbus_exception_probing"][0]
                self.assertTrue(exception_result.response_is_exception)
                self.assertEqual(exception_result.exception_code, 2)
            finally:
                _stop_server(modbus_server)
                _stop_server(hmi_server)

    def test_runner_passes_generated_attack_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_log = Path(directory) / "events.jsonl"
            state = _ProcessState(event_log_path=event_log)
            modbus_server = _start_modbus_server(state)
            hmi_server = _start_hmi_server(state)
            try:
                options = ModbusAttackGenerationOptions(
                    seed=9,
                    transaction_start=900,
                    scan_stop=3,
                    setpoint_values=(600,),
                    coil_values=(True, False),
                )
                cases = build_generated_modbus_attack_cases(options)

                report = LiveModbusBenchmarkRunner(
                    host="127.0.0.1",
                    port=modbus_server.server_address[1],
                    hmi_observer=HMIStateObserver(
                        f"http://127.0.0.1:{hmi_server.server_address[1]}/api/state"
                    ),
                    event_log_path=event_log,
                ).run(cases)

                self.assertTrue(report.passed)
                self.assertEqual(report.total_steps, 31)
                self.assertEqual(
                    report.status_counts(),
                    {"allowed": 30, "anomalous": 1},
                )
            finally:
                _stop_server(modbus_server)
                _stop_server(hmi_server)

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
        self.level_percent = 48.0
        self.pressure_bar = 1.2
        self.mode = "auto"
        self.outlet_pump_running = False
        self.inlet_valve_open = True
        self.high_level_alarm = False
        self.event_log_path = event_log_path
        self.transaction_signatures_by_session: dict[str, dict[int, str]] = {}


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class _ModbusHandler(socketserver.BaseRequestHandler):
    server: "_ModbusServer"

    def handle(self) -> None:
        while True:
            try:
                header = _recv_exact(self.request, 7)
                length = int.from_bytes(header[4:6], "big")
                request = header + _recv_exact(self.request, length - 1)
                parsed = parse_modbus_tcp_request(request)
            except ConnectionError:
                return

            self.server.state.append_event(
                request.hex(),
                session_key=f"{self.client_address[0]}:{self.client_address[1]}",
            )
            response_hex = self.server.state.response_for(parsed)
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

    def response_for(self, parsed) -> str:
        if parsed.function_code in {1, 2, 3, 4}:
            values = self._read_values(parsed.function_code, parsed.address, parsed.count)
            if values is None:
                return build_modbus_tcp_response_from_request(parsed, exception_code=2)
            return build_modbus_tcp_response_from_request(parsed, values=values)

        if parsed.function_code == 5 and parsed.address is not None and parsed.values:
            if parsed.address == 0:
                self.process_state.outlet_pump_running = parsed.values[0] == 0xFF00
                return build_modbus_tcp_response_from_request(parsed)
            if parsed.address == 1:
                self.process_state.inlet_valve_open = parsed.values[0] == 0xFF00
                return build_modbus_tcp_response_from_request(parsed)
            return build_modbus_tcp_response_from_request(parsed, exception_code=2)

        if parsed.function_code == 6 and parsed.address is not None and parsed.values:
            if parsed.address == 0:
                self.process_state.level_setpoint_percent = parsed.values[0] / 10.0
                return build_modbus_tcp_response_from_request(parsed)
            if parsed.address == 1:
                mode = {0: "stop", 1: "manual", 2: "auto", 3: "fault"}.get(
                    parsed.values[0]
                )
                if mode is None:
                    return build_modbus_tcp_response_from_request(parsed, exception_code=3)
                self.process_state.mode = mode
                return build_modbus_tcp_response_from_request(parsed)
            return build_modbus_tcp_response_from_request(parsed, exception_code=2)

        if parsed.function_code == 15 and parsed.address is not None:
            if parsed.address == 0 and parsed.count == 2:
                self.process_state.outlet_pump_running = bool(parsed.values[0])
                self.process_state.inlet_valve_open = bool(parsed.values[1])
                return build_modbus_tcp_response_from_request(parsed)
            return build_modbus_tcp_response_from_request(parsed, exception_code=2)

        if parsed.function_code == 16 and parsed.address is not None:
            if parsed.address == 0 and parsed.count == 2:
                self.process_state.level_setpoint_percent = parsed.values[0] / 10.0
                mode = {0: "stop", 1: "manual", 2: "auto", 3: "fault"}.get(
                    parsed.values[1]
                )
                if mode is None:
                    return build_modbus_tcp_response_from_request(parsed, exception_code=3)
                self.process_state.mode = mode
                return build_modbus_tcp_response_from_request(parsed)
            return build_modbus_tcp_response_from_request(parsed, exception_code=2)

        return build_modbus_tcp_exception_response(
            transaction_id=parsed.transaction_id,
            unit_id=parsed.unit_id,
            function_code=parsed.function_code,
            exception_code=1,
        )

    def _read_values(
        self,
        function_code: int,
        address: int | None,
        count: int | None,
    ) -> list[int] | None:
        if address is None or count is None:
            return None
        blocks = {
            1: [
                int(self.process_state.outlet_pump_running),
                int(self.process_state.inlet_valve_open),
            ],
            2: [int(self.process_state.high_level_alarm)],
            3: [
                int(round(self.process_state.level_setpoint_percent * 10)),
                {"stop": 0, "manual": 1, "auto": 2, "fault": 3}[
                    self.process_state.mode
                ],
            ],
            4: [
                int(round(self.process_state.level_percent * 10)),
                int(round(self.process_state.pressure_bar * 100)),
            ],
        }
        block = blocks[function_code]
        if address < 0 or address + count > len(block):
            return None
        return block[address : address + count]

    def append_event(self, request_hex: str, *, session_key: str) -> None:
        if self.process_state.event_log_path is None:
            return
        parsed = parse_modbus_tcp_request(request_hex)
        signature = request_hex[4:]
        signatures = self.process_state.transaction_signatures_by_session.setdefault(
            session_key,
            {},
        )
        previous_signature = signatures.get(
            parsed.transaction_id
        )
        status = (
            "anomalous"
            if previous_signature is not None and previous_signature != signature
            else "allowed"
        )
        signatures[parsed.transaction_id] = signature
        with self.process_state.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "metadata": {
                            "request_hex": request_hex,
                            "protocol_fsm_status": status,
                            "protocol_fsm_reason": f"test_{status}",
                        }
                    },
                    sort_keys=True,
                )
                + "\n"
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
                "level_percent": self.server.state.level_percent,
                "pressure_bar": self.server.state.pressure_bar,
                "mode": self.server.state.mode,
                "outlet_pump_running": self.server.state.outlet_pump_running,
                "inlet_valve_open": self.server.state.inlet_valve_open,
                "high_level_alarm": self.server.state.high_level_alarm,
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
