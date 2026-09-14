from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_plc.evaluation import (
    ModbusAttackGenerationOptions,
    benchmark_cases_to_payload,
    build_generated_modbus_attack_cases,
    modbus_attack_options_from_scenario,
    modbus_attack_generation_metadata,
    write_benchmark_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic attack-like live Modbus benchmark cases. "
            "The output uses the same JSON schema consumed by "
            "tools/run_live_modbus_benchmark.py."
        )
    )
    parser.add_argument("--output", type=Path, help="Optional benchmark JSON path.")
    parser.add_argument(
        "--scenario",
        type=Path,
        help=(
            "Optional physical-process scenario JSON. When supplied, Modbus "
            "tables and writable points are inferred from the scenario mapping."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print JSON to stdout when --output is supplied.",
    )
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument(
        "--profile",
        help=(
            "Optional profile label for metadata. Defaults to tank_pump without "
            "--scenario, or the scenario_id when --scenario is supplied."
        ),
    )
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--transaction-start", type=int, default=700)
    parser.add_argument("--scan-start", type=int, default=0)
    parser.add_argument(
        "--scan-stop",
        type=int,
        help=(
            "Exclusive upper bound for generated reconnaissance addresses. "
            "Defaults to 6 without --scenario, or max mapped address plus two "
            "with --scenario."
        ),
    )
    parser.add_argument(
        "--setpoint",
        type=int,
        action="append",
        dest="setpoints",
        help=(
            "Encoded level setpoint to write, e.g. 650 means 65.0%%. "
            "May be provided multiple times."
        ),
    )
    parser.add_argument(
        "--coil",
        choices=("on", "off"),
        action="append",
        dest="coils",
        help="Pump coil command to include. May be provided multiple times.",
    )
    parser.add_argument("--no-recon-sweep", action="store_true")
    parser.add_argument("--no-write-readback", action="store_true")
    parser.add_argument("--no-batch-writes", action="store_true")
    parser.add_argument("--no-exception-probing", action="store_true")
    parser.add_argument("--no-transaction-abuse", action="store_true")
    parser.add_argument("--no-multi-session-interleave", action="store_true")
    args = parser.parse_args()

    setpoints = tuple(args.setpoints) if args.setpoints is not None else None
    coils = tuple(value == "on" for value in args.coils) if args.coils else None
    feature_options = {
        "include_recon_sweep": not args.no_recon_sweep,
        "include_write_readback": not args.no_write_readback,
        "include_batch_writes": not args.no_batch_writes,
        "include_exception_probing": not args.no_exception_probing,
        "include_transaction_abuse": not args.no_transaction_abuse,
        "include_multi_session_interleave": not args.no_multi_session_interleave,
    }
    if args.scenario is not None:
        options = modbus_attack_options_from_scenario(
            args.scenario,
            seed=args.seed,
            profile=args.profile,
            unit_id=args.unit_id,
            transaction_start=args.transaction_start,
            scan_start=args.scan_start,
            scan_stop=args.scan_stop,
            setpoint_values=setpoints,
            coil_values=coils,
            **feature_options,
        )
    else:
        options = ModbusAttackGenerationOptions(
            seed=args.seed,
            profile=args.profile or "tank_pump",
            unit_id=args.unit_id,
            transaction_start=args.transaction_start,
            scan_start=args.scan_start,
            scan_stop=args.scan_stop if args.scan_stop is not None else 6,
            setpoint_values=tuple(setpoints or (550, 720, 650)),
            coil_values=tuple(coils or (True, False, True)),
            **feature_options,
        )
    cases = build_generated_modbus_attack_cases(options)
    payload = benchmark_cases_to_payload(cases)
    payload["metadata"] = modbus_attack_generation_metadata(options)
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        write_benchmark_payload(args.output, payload)
    if not args.quiet or args.output is None:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
