from __future__ import annotations

import argparse
import json
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProbeCase:
    name: str
    description: str
    payload: bytes
    expect_response: bool = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Send controlled Modbus TCP anomaly probes to a lab PLC/simulator. "
            "Use only against systems you own or are authorized to test."
        )
    )
    parser.add_argument("--target", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument(
        "--scenario",
        choices=("safe", "pulse-coil", "map-coils", "disturbance", "read-coils"),
        default="safe",
        help=(
            "safe sends malformed/exception-triggering requests; pulse-coil "
            "temporarily writes one coil and restores it; map-coils pulses a "
            "range and observes Factory I/O tag deltas; disturbance toggles a "
            "set of coils to visibly perturb the simulation; read-coils only "
            "reads current coil values."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send packets. Without this flag, only print the plan.",
    )
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--factoryio-url", default="http://127.0.0.1:7410")
    parser.add_argument("--coil-address", type=int, default=1)
    parser.add_argument("--coil-value", choices=("on", "off"), default="off")
    parser.add_argument("--pulse-seconds", type=float, default=1.0)
    parser.add_argument("--no-restore", action="store_true")
    parser.add_argument(
        "--addresses",
        default="0-28",
        help="Comma-separated addresses and ranges, e.g. 0-6,26-28.",
    )
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()

    if args.scenario == "safe":
        run_safe(args)
    elif args.scenario == "pulse-coil":
        run_pulse_coil(args)
    elif args.scenario == "map-coils":
        run_map_coils(args)
    elif args.scenario == "read-coils":
        run_read_coils(args)
    else:
        run_disturbance(args)


def run_safe(args: argparse.Namespace) -> None:
    cases = safe_cases(unit_id=args.unit_id)
    print(
        json.dumps(
            {
                "target": args.target,
                "port": args.port,
                "unit_id": args.unit_id,
                "scenario": "safe",
                "execute": args.execute,
                "case_count": len(cases),
            },
            ensure_ascii=False,
        )
    )
    for case in cases:
        print_case_plan(case)

    if not args.execute:
        return

    before = read_factoryio_values(args.factoryio_url)
    results = []
    for case in cases:
        time.sleep(args.delay)
        started = time.time()
        response, error = send_raw(
            args.target,
            args.port,
            case.payload,
            timeout=args.timeout,
        )
        results.append(
            {
                "name": case.name,
                "elapsed_ms": round((time.time() - started) * 1000, 2),
                "request_hex": case.payload.hex(),
                "response_hex": response.hex() if response else None,
                "response": describe_response(response),
                "error": error,
            }
        )
    after = read_factoryio_values(args.factoryio_url)
    changed = diff_values(before, after)
    print(
        json.dumps(
            {
                "results": results,
                "factoryio_changed_values": changed[:50],
                "factoryio_changed_count": len(changed),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def run_pulse_coil(args: argparse.Namespace) -> None:
    desired = args.coil_value == "on"
    print(
        json.dumps(
            {
                "target": args.target,
                "port": args.port,
                "unit_id": args.unit_id,
                "scenario": "pulse-coil",
                "execute": args.execute,
                "coil_address": args.coil_address,
                "coil_value": desired,
                "pulse_seconds": args.pulse_seconds,
                "restore": not args.no_restore,
            },
            ensure_ascii=False,
        )
    )
    if not args.execute:
        print(
            "Dry run only. Add --execute to write the coil. "
            "This mode can visibly alter the Factory I/O scene."
        )
        return

    before_values = read_factoryio_values(args.factoryio_url)
    before_coil = read_single_coil(
        args.target,
        args.port,
        args.unit_id,
        args.coil_address,
        args.timeout,
    )
    if before_coil is None and not args.no_restore:
        print(
            json.dumps(
                {
                    "aborted": True,
                    "reason": (
                        "Could not read the original coil value, so the tool "
                        "will not write because it cannot restore safely. "
                        "Use --no-restore only for disposable lab tests."
                    ),
                    "coil_address": args.coil_address,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    write_response, write_error = send_raw(
        args.target,
        args.port,
        write_single_coil_frame(
            transaction_id=200,
            unit_id=args.unit_id,
            address=args.coil_address,
            value=desired,
        ),
        timeout=args.timeout,
    )
    time.sleep(args.pulse_seconds)
    restore_response = None
    restore_error = None
    if not args.no_restore and before_coil is not None:
        restore_response, restore_error = send_raw(
            args.target,
            args.port,
            write_single_coil_frame(
                transaction_id=201,
                unit_id=args.unit_id,
                address=args.coil_address,
                value=before_coil,
            ),
            timeout=args.timeout,
        )
    after_values = read_factoryio_values(args.factoryio_url)
    print(
        json.dumps(
            {
                "before_coil": before_coil,
                "write_response": describe_response(write_response),
                "write_response_hex": write_response.hex() if write_response else None,
                "write_error": write_error,
                "restore_response": describe_response(restore_response),
                "restore_response_hex": (
                    restore_response.hex() if restore_response else None
                ),
                "restore_error": restore_error,
                "factoryio_changed_values": diff_values(before_values, after_values)[:50],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def run_read_coils(args: argparse.Namespace) -> None:
    addresses = parse_addresses(args.addresses)
    results = [
        {
            "address": address,
            "value": read_single_coil(
                args.target,
                args.port,
                args.unit_id,
                address,
                args.timeout,
            ),
        }
        for address in addresses
    ]
    print(
        json.dumps(
            {
                "target": args.target,
                "port": args.port,
                "unit_id": args.unit_id,
                "scenario": "read-coils",
                "addresses": addresses,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def run_map_coils(args: argparse.Namespace) -> None:
    addresses = parse_addresses(args.addresses)
    print(
        json.dumps(
            {
                "target": args.target,
                "port": args.port,
                "unit_id": args.unit_id,
                "scenario": "map-coils",
                "execute": args.execute,
                "addresses": addresses,
                "pulse_seconds": args.pulse_seconds,
                "restore": not args.no_restore,
            },
            ensure_ascii=False,
        )
    )
    if not args.execute:
        print(
            "Dry run only. Add --execute to pulse each readable coil and "
            "observe Factory I/O tag deltas."
        )
        return

    results = []
    for index, address in enumerate(addresses):
        original = read_single_coil(
            args.target,
            args.port,
            args.unit_id,
            address,
            args.timeout,
        )
        if original is None:
            results.append(
                {
                    "address": address,
                    "readable": False,
                    "skipped": True,
                    "reason": "original coil value could not be read",
                }
            )
            continue

        before_values = read_factoryio_values(args.factoryio_url)
        target_value = not original
        write_response, write_error = send_raw(
            args.target,
            args.port,
            write_single_coil_frame(
                transaction_id=1000 + index * 2,
                unit_id=args.unit_id,
                address=address,
                value=target_value,
            ),
            timeout=args.timeout,
        )
        time.sleep(args.pulse_seconds)
        during_values = read_factoryio_values(args.factoryio_url)

        restore_response = None
        restore_error = None
        if not args.no_restore:
            restore_response, restore_error = send_raw(
                args.target,
                args.port,
                write_single_coil_frame(
                    transaction_id=1001 + index * 2,
                    unit_id=args.unit_id,
                    address=address,
                    value=original,
                ),
                timeout=args.timeout,
            )
            time.sleep(max(0.05, min(args.delay, 0.5)))
        after_values = read_factoryio_values(args.factoryio_url)

        results.append(
            {
                "address": address,
                "readable": True,
                "original": original,
                "pulsed_value": target_value,
                "write_response": describe_response(write_response),
                "write_error": write_error,
                "restore_response": describe_response(restore_response),
                "restore_error": restore_error,
                "changed_during": diff_values(before_values, during_values)[:30],
                "changed_after_restore": diff_values(before_values, after_values)[:30],
            }
        )
        time.sleep(args.delay)

    print(json.dumps({"results": results}, indent=2, ensure_ascii=False))


def run_disturbance(args: argparse.Namespace) -> None:
    addresses = parse_addresses(args.addresses)
    print(
        json.dumps(
            {
                "target": args.target,
                "port": args.port,
                "unit_id": args.unit_id,
                "scenario": "disturbance",
                "execute": args.execute,
                "addresses": addresses,
                "rounds": args.rounds,
                "pulse_seconds": args.pulse_seconds,
                "restore": not args.no_restore,
            },
            ensure_ascii=False,
        )
    )
    if not args.execute:
        print(
            "Dry run only. Add --execute to toggle the listed coils. "
            "This mode is intentionally disruptive to the simulation."
        )
        return
    if args.rounds <= 0:
        raise SystemExit("--rounds must be positive")

    original_values: dict[int, bool] = {}
    unreadable: list[int] = []
    for address in addresses:
        original = read_single_coil(
            args.target,
            args.port,
            args.unit_id,
            address,
            args.timeout,
        )
        if original is None:
            unreadable.append(address)
        else:
            original_values[address] = original

    if not original_values:
        print(
            json.dumps(
                {
                    "aborted": True,
                    "reason": "none of the requested coils could be read",
                    "unreadable": unreadable,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    before_values = read_factoryio_values(args.factoryio_url)
    actions = []
    tx_id = 3000
    for round_index in range(args.rounds):
        for position, address in enumerate(addresses):
            if address not in original_values:
                continue
            value = ((round_index + position) % 2) == 0
            response, error = send_raw(
                args.target,
                args.port,
                write_single_coil_frame(
                    transaction_id=tx_id,
                    unit_id=args.unit_id,
                    address=address,
                    value=value,
                ),
                timeout=args.timeout,
            )
            tx_id += 1
            actions.append(
                {
                    "round": round_index + 1,
                    "address": address,
                    "value": value,
                    "response": describe_response(response),
                    "error": error,
                }
            )
            time.sleep(args.pulse_seconds)

    disturbed_values = read_factoryio_values(args.factoryio_url)
    restore_actions = []
    if not args.no_restore:
        for address, original in original_values.items():
            response, error = send_raw(
                args.target,
                args.port,
                write_single_coil_frame(
                    transaction_id=tx_id,
                    unit_id=args.unit_id,
                    address=address,
                    value=original,
                ),
                timeout=args.timeout,
            )
            tx_id += 1
            restore_actions.append(
                {
                    "address": address,
                    "value": original,
                    "response": describe_response(response),
                    "error": error,
                }
            )
            time.sleep(max(0.05, min(args.delay, 0.5)))
    after_values = read_factoryio_values(args.factoryio_url)

    print(
        json.dumps(
            {
                "readable_addresses": sorted(original_values),
                "unreadable_addresses": unreadable,
                "actions": actions,
                "changed_during_disturbance": diff_values(
                    before_values,
                    disturbed_values,
                )[:80],
                "restore_actions": restore_actions,
                "changed_after_restore": diff_values(before_values, after_values)[:80],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def safe_cases(*, unit_id: int) -> list[ProbeCase]:
    return [
        ProbeCase(
            "valid_read_coils_baseline",
            "Small valid read used as a connectivity baseline.",
            read_bits_frame(
                transaction_id=1,
                unit_id=unit_id,
                function_code=0x01,
                address=0,
                quantity=16,
            ),
        ),
        ProbeCase(
            "illegal_function_0x7f",
            "Unsupported function code should return a Modbus exception.",
            mbap(transaction_id=2, unit_id=unit_id, pdu=b"\x7f\x00\x00"),
        ),
        ProbeCase(
            "illegal_coil_address_high",
            "Read a high coil address likely outside the mapped process image.",
            read_bits_frame(
                transaction_id=3,
                unit_id=unit_id,
                function_code=0x01,
                address=65000,
                quantity=16,
            ),
        ),
        ProbeCase(
            "oversized_read_coils",
            "Request more coils than the Modbus specification allows.",
            read_bits_frame(
                transaction_id=4,
                unit_id=unit_id,
                function_code=0x01,
                address=0,
                quantity=3000,
            ),
        ),
        ProbeCase(
            "invalid_protocol_id",
            "MBAP protocol id is not zero; compliant servers should reject/ignore.",
            mbap(
                transaction_id=5,
                unit_id=unit_id,
                pdu=b"\x01\x00\x00\x00\x10",
                protocol_id=1,
            ),
            expect_response=False,
        ),
        ProbeCase(
            "bad_mbap_length_too_short",
            "MBAP length says only unit id is present although PDU bytes follow.",
            struct.pack(">HHHB", 6, 0, 1, unit_id) + b"\x01\x00\x00\x00\x10",
            expect_response=False,
        ),
        ProbeCase(
            "trailing_bytes_after_valid_request",
            "Valid request with extra bytes appended after the declared MBAP length.",
            read_bits_frame(
                transaction_id=7,
                unit_id=unit_id,
                function_code=0x01,
                address=0,
                quantity=8,
            )
            + b"\xde\xad\xbe\xef",
        ),
        ProbeCase(
            "write_multiple_coils_bad_byte_count",
            "Malformed FC15 body with a byte count inconsistent with quantity.",
            mbap(
                transaction_id=8,
                unit_id=unit_id,
                pdu=struct.pack(">BHHB", 0x0F, 0, 10, 1) + b"\xff\x00",
            ),
        ),
    ]


def print_case_plan(case: ProbeCase) -> None:
    print(
        json.dumps(
            {
                "case": case.name,
                "description": case.description,
                "expect_response": case.expect_response,
                "payload_hex": case.payload.hex(),
            },
            ensure_ascii=False,
        )
    )


def mbap(
    *,
    transaction_id: int,
    unit_id: int,
    pdu: bytes,
    protocol_id: int = 0,
) -> bytes:
    length = 1 + len(pdu)
    return struct.pack(">HHHB", transaction_id, protocol_id, length, unit_id) + pdu


def read_bits_frame(
    *,
    transaction_id: int,
    unit_id: int,
    function_code: int,
    address: int,
    quantity: int,
) -> bytes:
    return mbap(
        transaction_id=transaction_id,
        unit_id=unit_id,
        pdu=struct.pack(">BHH", function_code, address, quantity),
    )


def write_single_coil_frame(
    *,
    transaction_id: int,
    unit_id: int,
    address: int,
    value: bool,
) -> bytes:
    encoded = 0xFF00 if value else 0x0000
    return mbap(
        transaction_id=transaction_id,
        unit_id=unit_id,
        pdu=struct.pack(">BHH", 0x05, address, encoded),
    )


def parse_addresses(spec: str) -> list[int]:
    addresses: list[int] = []
    for part in spec.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            left, right = item.split("-", 1)
            start = int(left)
            end = int(right)
            if start > end:
                raise ValueError(f"invalid descending address range: {item}")
            addresses.extend(range(start, end + 1))
        else:
            addresses.append(int(item))
    deduped = sorted(set(addresses))
    if not deduped:
        raise ValueError("at least one address is required")
    if deduped[0] < 0 or deduped[-1] > 65535:
        raise ValueError("addresses must be between 0 and 65535")
    return deduped


def send_raw(
    host: str,
    port: int,
    payload: bytes,
    *,
    timeout: float,
) -> tuple[bytes | None, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
            try:
                return sock.recv(260), None
            except socket.timeout:
                return None, "timeout"
    except OSError as exc:
        return None, str(exc)


def read_single_coil(
    host: str,
    port: int,
    unit_id: int,
    address: int,
    timeout: float,
) -> bool | None:
    response, _ = send_raw(
        host,
        port,
        read_bits_frame(
            transaction_id=199,
            unit_id=unit_id,
            function_code=0x01,
            address=address,
            quantity=1,
        ),
        timeout=timeout,
    )
    if not response or len(response) < 10:
        return None
    pdu = response[7:]
    if len(pdu) >= 3 and pdu[0] == 0x01 and pdu[1] == 1:
        return bool(pdu[2] & 0x01)
    return None


def describe_response(response: bytes | None) -> dict[str, Any] | None:
    if not response:
        return None
    if len(response) < 8:
        return {"kind": "short_response", "length": len(response)}
    transaction_id, protocol_id, length, unit_id = struct.unpack(">HHHB", response[:7])
    pdu = response[7:]
    result: dict[str, Any] = {
        "transaction_id": transaction_id,
        "protocol_id": protocol_id,
        "length": length,
        "unit_id": unit_id,
        "pdu_hex": pdu.hex(),
    }
    if not pdu:
        result["kind"] = "empty_pdu"
        return result
    function_code = pdu[0]
    result["function_code"] = function_code
    if function_code & 0x80 and len(pdu) >= 2:
        result["kind"] = "exception"
        result["exception_code"] = pdu[1]
        result["exception_name"] = exception_name(pdu[1])
    else:
        result["kind"] = "normal"
    return result


def exception_name(code: int) -> str:
    return {
        1: "illegal_function",
        2: "illegal_data_address",
        3: "illegal_data_value",
        4: "server_device_failure",
        5: "acknowledge",
        6: "server_device_busy",
        8: "memory_parity_error",
        10: "gateway_path_unavailable",
        11: "gateway_target_failed_to_respond",
    }.get(code, f"unknown_{code}")


def read_factoryio_values(base_url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
            base_url.rstrip("/") + "/api/tags",
            timeout=2.0,
        ) as response:
            tags = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}
    return {tag["name"]: tag.get("value") for tag in tags if "name" in tag}


def diff_values(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changed = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changed.append(
                {
                    "name": key,
                    "before": before.get(key),
                    "after": after.get(key),
                }
            )
    return changed


if __name__ == "__main__":
    main()
