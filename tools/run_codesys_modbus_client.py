from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path
from typing import Sequence

from agentic_plc.acquisition import (
    build_cargo_sorting_acquisition_plan,
    learn_process_model,
    parse_modbus_interaction,
    write_modbus_trace_jsonl,
)
from agentic_plc.protocols.modbus import (
    build_modbus_tcp_read_request,
    build_modbus_tcp_write_single_coil_request,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a periodic Modbus TCP client against a CODESYS runtime and "
            "record request/response interactions for process acquisition."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--period-seconds", type=float, default=1.0)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=30.0,
        help="Run duration. Use 0 for continuous until Ctrl+C.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("records/codesys_periodic_client"),
    )
    parser.add_argument(
        "--write-pattern",
        choices=("none", "pulse-coils", "rotate-coils"),
        default="none",
        help=(
            "none only reads. pulse-coils turns one probe coil on and off in "
            "the same cycle. rotate-coils keeps one probe coil on per cycle and "
            "turns the previous one off."
        ),
    )
    parser.add_argument(
        "--write-coils",
        default="0,1,4,5,6",
        help="Comma-separated coil addresses used by write patterns.",
    )
    parser.add_argument(
        "--include-registers",
        action="store_true",
        help="Also read holding/input registers 0-9 each cycle.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print per-cycle snapshots.",
    )
    args = parser.parse_args()

    if args.period_seconds <= 0:
        raise ValueError("--period-seconds must be positive")
    if args.duration_seconds < 0:
        raise ValueError("--duration-seconds must be non-negative")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "interaction_trace.jsonl"
    model_path = output_dir / "learned_process_model.json"
    summary_path = output_dir / "summary.json"
    live_log_path = output_dir / "live_snapshots.jsonl"

    plan = build_cargo_sorting_acquisition_plan()
    scenario = plan.scenario_mapping()
    client = PersistentModbusTcpClient(args.host, args.port)
    tx = 3000
    start = time.monotonic()
    cycle_index = 0
    events = []
    live_snapshots = []
    write_coils = _parse_address_list(args.write_coils)
    active_rotating_coil: int | None = None

    def transact(request_hex: str, label: str):
        nonlocal tx
        response_hex = client.transact(request_hex)
        event = parse_modbus_interaction(
            request_hex,
            response_hex,
            timestamp=time.monotonic() - start,
            metadata={
                "source": "codesys_periodic_client",
                "label": label,
                "cycle_index": cycle_index,
                "host": args.host,
                "port": args.port,
                "write_pattern": args.write_pattern,
            },
        )
        events.append(event)
        return event

    def read(function_code: int, address: int, count: int, label: str):
        nonlocal tx
        tx += 1
        return transact(
            build_modbus_tcp_read_request(
                transaction_id=tx,
                unit_id=args.unit_id,
                function_code=function_code,
                address=address,
                count=count,
            ),
            label,
        )

    def write_coil(address: int, value: bool, label: str):
        nonlocal tx
        tx += 1
        return transact(
            build_modbus_tcp_write_single_coil_request(
                transaction_id=tx,
                unit_id=args.unit_id,
                address=address,
                energized=value,
            ),
            label,
        )

    stop_at = None if args.duration_seconds == 0 else start + args.duration_seconds
    interrupted = False

    try:
        while stop_at is None or time.monotonic() < stop_at:
            cycle_started = time.monotonic()
            cycle_index += 1

            write_events = []
            if args.write_pattern == "pulse-coils":
                address = write_coils[(cycle_index - 1) % len(write_coils)]
                write_events.append(write_coil(address, True, f"pulse_coil_{address}_on"))
                write_events.append(write_coil(address, False, f"pulse_coil_{address}_off"))
            elif args.write_pattern == "rotate-coils":
                next_address = write_coils[(cycle_index - 1) % len(write_coils)]
                if active_rotating_coil is not None and active_rotating_coil != next_address:
                    write_events.append(
                        write_coil(
                            active_rotating_coil,
                            False,
                            f"rotate_coil_{active_rotating_coil}_off",
                        )
                    )
                active_rotating_coil = next_address
                write_events.append(write_coil(next_address, True, f"rotate_coil_{next_address}_on"))

            coils_event = read(1, 0, 11, "read_coils_0_10")
            discrete_event = read(2, 0, 7, "read_discrete_inputs_0_6")
            register_events = []
            if args.include_registers:
                register_events.append(read(3, 0, 10, "read_holding_registers_0_9"))
                register_events.append(read(4, 0, 10, "read_input_registers_0_9"))

            snapshot = {
                "cycle_index": cycle_index,
                "elapsed_seconds": time.monotonic() - start,
                "coils_0_10": list(coils_event.response_values),
                "discrete_inputs_0_6": list(discrete_event.response_values),
                "write_labels": [event.metadata.get("label") for event in write_events],
            }
            if register_events:
                snapshot["register_reads"] = {
                    str(event.metadata.get("label")): list(event.response_values)
                    for event in register_events
                }
            live_snapshots.append(snapshot)
            _append_jsonl(live_log_path, snapshot)
            if not args.quiet:
                print(json.dumps(snapshot, sort_keys=True), flush=True)

            remaining = args.period_seconds - (time.monotonic() - cycle_started)
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if args.write_pattern == "rotate-coils" and active_rotating_coil is not None:
            try:
                write_coil(active_rotating_coil, False, f"final_restore_coil_{active_rotating_coil}_off")
            except Exception:
                pass
        client.close()

    model = learn_process_model(
        scenario,
        events,
        variables=plan.process_variables(),
    )
    write_modbus_trace_jsonl(trace_path, events)
    model_path.write_text(
        json.dumps(model.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "host": args.host,
        "port": args.port,
        "duration_seconds": time.monotonic() - start,
        "period_seconds": args.period_seconds,
        "interrupted": interrupted,
        "cycle_count": cycle_index,
        "event_count": len(events),
        "write_pattern": args.write_pattern,
        "observed_operation_counts": dict(model.observed_operation_counts),
        "response_example_count": len(model.response_examples),
        "write_effect_count": len(model.write_effects),
        "artifacts": {
            "interaction_trace": str(trace_path),
            "learned_process_model": str(model_path),
            "live_snapshots": str(live_log_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not args.quiet:
        print(json.dumps({"summary": summary}, indent=2, sort_keys=True))
    return 0


class PersistentModbusTcpClient:
    def __init__(self, host: str, port: int, *, timeout: float = 2.0) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._socket: socket.socket | None = None

    def transact(self, request_hex: str) -> str:
        request = bytes.fromhex(_clean_hex(request_hex))
        try:
            return self._transact_once(request)
        except (OSError, ConnectionError):
            self.close()
            return self._transact_once(request)

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def _transact_once(self, request: bytes) -> str:
        sock = self._ensure_socket()
        sock.sendall(request)
        header = _recv_exact(sock, 7)
        length = int.from_bytes(header[4:6], "big")
        body = _recv_exact(sock, length - 1)
        return (header + body).hex()

    def _ensure_socket(self) -> socket.socket:
        if self._socket is None:
            self._socket = socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
            )
            self._socket.settimeout(self.timeout)
        return self._socket


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed before full Modbus response")
        chunks.extend(chunk)
    return bytes(chunks)


def _parse_address_list(text: str) -> tuple[int, ...]:
    values = tuple(
        int(part.strip())
        for part in str(text).split(",")
        if part.strip()
    )
    if not values:
        raise ValueError("address list must not be empty")
    if any(address < 0 for address in values):
        raise ValueError("coil addresses must be non-negative")
    return values


def _append_jsonl(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _clean_hex(value: str) -> str:
    return "".join(str(value).split())


if __name__ == "__main__":
    raise SystemExit(main())
