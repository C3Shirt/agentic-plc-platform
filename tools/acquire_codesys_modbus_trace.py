from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path
from typing import Iterable

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
            "Acquire Modbus TCP request/response traces from a local CODESYS "
            "runtime and convert them into the project acquisition artifacts."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("records/codesys_cargo_sorting_acquisition"),
    )
    parser.add_argument(
        "--sequence",
        choices=("observe", "cargo-sorter-probe"),
        default="cargo-sorter-probe",
        help=(
            "observe only reads PLC-visible tables; cargo-sorter-probe also "
            "toggles selected coils and restores them at the end."
        ),
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=0.25,
        help="Delay between Modbus operations.",
    )
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="Leave written coils in their final probe state.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    client = ModbusTcpClient(args.host, args.port)
    plan = build_cargo_sorting_acquisition_plan()
    scenario = plan.scenario_mapping()
    events = []
    tx = 2000
    start = time.monotonic()

    def transact(request_hex: str, label: str) -> None:
        nonlocal tx
        response_hex = client.transact(request_hex)
        events.append(
            parse_modbus_interaction(
                request_hex,
                response_hex,
                timestamp=time.monotonic() - start,
                metadata={
                    "source": "codesys_modbus_live",
                    "sequence": args.sequence,
                    "label": label,
                    "host": args.host,
                    "port": args.port,
                },
            )
        )
        time.sleep(max(0.0, args.settle_seconds))

    def read(function_code: int, address: int, count: int, label: str) -> None:
        nonlocal tx
        tx += 1
        transact(
            build_modbus_tcp_read_request(
                transaction_id=tx,
                unit_id=args.unit_id,
                function_code=function_code,
                address=address,
                count=count,
            ),
            label,
        )

    def write_coil(address: int, value: bool, label: str) -> None:
        nonlocal tx
        tx += 1
        transact(
            build_modbus_tcp_write_single_coil_request(
                transaction_id=tx,
                unit_id=args.unit_id,
                address=address,
                energized=value,
            ),
            label,
        )

    # The user's current CODESYS configuration exposes these live ranges.
    read(1, 0, 11, "baseline_coils_0_10")
    read(2, 0, 7, "baseline_discrete_inputs_0_6")
    read(3, 0, 10, "baseline_holding_registers_0_9")
    read(4, 0, 10, "baseline_input_registers_0_9")

    if args.sequence == "cargo-sorter-probe":
        probe_addresses = (0, 1, 4, 5, 6)
        for address in probe_addresses:
            write_coil(address, True, f"force_coil_{address}_on")
            read(1, 0, 11, f"read_coils_after_{address}_on")
            read(2, 0, 7, f"read_discrete_after_{address}_on")

        # Let the PLC logic scan and any attached simulator settle.
        time.sleep(max(0.0, args.settle_seconds * 2))
        read(1, 0, 11, "settled_coils_after_on_probe")
        read(2, 0, 7, "settled_discrete_after_on_probe")

        if not args.no_restore:
            for address in probe_addresses:
                write_coil(address, False, f"restore_coil_{address}_off")
            read(1, 0, 11, "coils_after_restore")
            read(2, 0, 7, "discrete_after_restore")

    model = learn_process_model(
        scenario,
        events,
        variables=plan.process_variables(),
    )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "interaction_trace.jsonl"
    model_path = output_dir / "learned_process_model.json"
    summary_path = output_dir / "summary.json"
    scenario_path = output_dir / "scenario.json"
    plan_path = output_dir / "acquisition_plan.json"

    write_modbus_trace_jsonl(trace_path, events)
    model_path.write_text(
        json.dumps(model.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    scenario_path.write_text(
        json.dumps(scenario.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    plan_path.write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "host": args.host,
        "port": args.port,
        "sequence": args.sequence,
        "event_count": len(events),
        "response_example_count": len(model.response_examples),
        "write_effect_count": len(model.write_effects),
        "observed_operation_counts": dict(model.observed_operation_counts),
        "artifacts": {
            "acquisition_plan": str(plan_path),
            "scenario": str(scenario_path),
            "interaction_trace": str(trace_path),
            "learned_process_model": str(model_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not args.quiet:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


class ModbusTcpClient:
    def __init__(self, host: str, port: int, *, timeout: float = 2.0) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)

    def transact(self, request_hex: str) -> str:
        request = bytes.fromhex(_clean_hex(request_hex))
        with socket.create_connection((self.host, self.port), self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(request)
            header = _recv_exact(sock, 7)
            length = int.from_bytes(header[4:6], "big")
            body = _recv_exact(sock, length - 1)
            return (header + body).hex()


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed before full Modbus response")
        chunks.extend(chunk)
    return bytes(chunks)


def _clean_hex(value: str) -> str:
    return "".join(str(value).split())


if __name__ == "__main__":
    raise SystemExit(main())
