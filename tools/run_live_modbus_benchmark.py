from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_plc.evaluation import (
    HMIStateObserver,
    LiveModbusBenchmarkRunner,
    build_default_live_modbus_cases,
    hmi_register_bindings_from_scenario,
    load_benchmark_cases,
    write_benchmark_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run benchmark cases against a live Modbus TCP honeypot endpoint. "
            "Unlike the offline runner, this sends real TCP bytes and can "
            "optionally compare replies against an HMI process-state snapshot "
            "and the honeypot JSONL event log."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1502)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument(
        "--input",
        type=Path,
        help=(
            "Benchmark JSON containing a top-level cases list. If omitted, "
            "the built-in live Modbus suite is used."
        ),
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        help=(
            "Optional physical-process scenario JSON. When supplied with "
            "--hmi-state-url, read-reply physical invariants use scenario "
            "register bindings instead of the built-in tank-pump bindings."
        ),
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only matching case_id values. May be provided multiple times.",
    )
    parser.add_argument(
        "--hmi-state-url",
        help=(
            "Optional JSON state endpoint, e.g. "
            "http://127.0.0.1:8080/api/state. When supplied, expected process "
            "values and physical invariants are checked from the live HMI view."
        ),
    )
    parser.add_argument(
        "--event-log",
        type=Path,
        help=(
            "Optional JSONL event log path produced by the honeypot. When "
            "supplied, protocol FSM status is checked against expected values."
        ),
    )
    parser.add_argument(
        "--event-log-timeout",
        type=float,
        default=1.0,
        help="Seconds to wait for a matching request event in --event-log.",
    )
    parser.add_argument(
        "--no-reuse-connection-per-case",
        action="store_true",
        help=(
            "Open a fresh TCP connection for every step. By default, steps in "
            "one case reuse a connection so session-level protocol behavior can "
            "be evaluated."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path for cases plus live_report.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Exit with status 0 even when live benchmark checks fail.",
    )
    args = parser.parse_args()

    cases = (
        load_benchmark_cases(args.input)
        if args.input is not None
        else build_default_live_modbus_cases()
    )
    if args.case_id:
        wanted = set(args.case_id)
        cases = tuple(case for case in cases if case.case_id in wanted)

    observer = None
    if args.hmi_state_url:
        observer_kwargs = {}
        if args.scenario is not None:
            observer_kwargs["register_bindings"] = hmi_register_bindings_from_scenario(
                args.scenario
            )
        observer = HMIStateObserver(
            args.hmi_state_url,
            timeout_seconds=args.timeout,
            **observer_kwargs,
        )
    report = LiveModbusBenchmarkRunner(
        host=args.host,
        port=args.port,
        timeout_seconds=args.timeout,
        hmi_observer=observer,
        event_log_path=args.event_log,
        event_log_timeout_seconds=args.event_log_timeout,
        reuse_connection_per_case=not args.no_reuse_connection_per_case,
    ).run(cases)
    payload = {
        "cases": [case.to_dict() for case in cases],
        "live_report": report.to_dict(),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        write_benchmark_payload(args.output, payload)
    print(text)
    if report.passed or args.allow_failures:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
