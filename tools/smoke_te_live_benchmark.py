from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_plc.evaluation import (
    HMIStateObserver,
    LiveModbusBenchmarkRunner,
    build_generated_modbus_attack_cases,
    hmi_register_bindings_from_scenario,
    modbus_attack_options_from_scenario,
    start_process_hmi_server,
    start_process_mapped_modbus_server,
    write_benchmark_payload,
)
from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a self-contained live Modbus/HMI benchmark smoke against the "
            "Tennessee Eastman scenario mapping."
        )
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=Path("external/tennessee_eastman"),
        help="Directory populated by download_tennessee_eastman.py.",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/tennessee_eastman/scenario.json"),
        help="TE scenario JSON mapping.",
    )
    parser.add_argument("--idv", default="idv1")
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument(
        "--scan-stop",
        type=int,
        default=3,
        help="Compact scan upper bound for a fast smoke run.",
    )
    parser.add_argument(
        "--tick-seconds",
        type=float,
        default=600.0,
        help="Advance the TE backend before running the live benchmark.",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Force a temporary synthetic TE-shaped trace instead of local assets.",
    )
    parser.add_argument(
        "--require-assets",
        action="store_true",
        help="Fail if downloaded TE assets are missing instead of using a synthetic trace.",
    )
    parser.add_argument(
        "--event-log",
        type=Path,
        help="Optional JSONL protocol event output path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path for cases plus live_report.",
    )
    parser.add_argument(
        "--full-output",
        action="store_true",
        help="Print the full benchmark payload instead of a compact summary.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Exit with status 0 even when benchmark checks fail.",
    )
    args = parser.parse_args()

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        scenario = ScenarioMapping.from_file(args.scenario)
        backend_source = "downloaded_assets"
        if args.synthetic or not _te_assets_exist(args.asset_root, args.idv):
            if args.require_assets and not args.synthetic:
                print(
                    "missing TE assets; run: python tools\\download_tennessee_eastman.py",
                    file=sys.stderr,
                )
                return 2
            temp_dir = tempfile.TemporaryDirectory()
            trace_dir = Path(temp_dir.name) / "te_synthetic_idv"
            _write_synthetic_te_trace(trace_dir)
            backend = TennesseeEastmanTraceBackend.from_directory(
                trace_dir,
                idv=f"{args.idv}_synthetic",
            )
            backend_source = "synthetic_trace"
        else:
            backend = TennesseeEastmanTraceBackend.from_asset_root(
                args.asset_root,
                idv=args.idv,
            )

        register_map = ProcessRegisterMap(backend, scenario)
        backend.tick(args.tick_seconds)

        if args.event_log is None and temp_dir is None:
            temp_dir = tempfile.TemporaryDirectory()
        event_log_path = args.event_log or (
            Path(temp_dir.name) / "te_live_benchmark_events.jsonl"
        )
        modbus_server = start_process_mapped_modbus_server(
            register_map,
            event_log_path=event_log_path,
        )
        hmi_server = start_process_hmi_server(
            backend,
            scenario_id=scenario.scenario_id,
        )
        try:
            options = modbus_attack_options_from_scenario(
                args.scenario,
                seed=args.seed,
                scan_stop=args.scan_stop,
            )
            cases = build_generated_modbus_attack_cases(options)
            report = LiveModbusBenchmarkRunner(
                host=modbus_server.host,
                port=modbus_server.port,
                hmi_observer=HMIStateObserver(
                    f"{hmi_server.url}/api/state",
                    register_bindings=hmi_register_bindings_from_scenario(args.scenario),
                ),
                event_log_path=event_log_path,
            ).run(cases)
        finally:
            modbus_server.stop()
            hmi_server.stop()

        payload = {
            "backend_source": backend_source,
            "scenario_id": scenario.scenario_id,
            "modbus_endpoint": f"{modbus_server.host}:{modbus_server.port}",
            "hmi_state_url": f"{hmi_server.url}/api/state",
            "event_log": str(event_log_path),
            "cases": [case.to_dict() for case in cases],
            "live_report": report.to_dict(),
        }
        if args.output is not None:
            write_benchmark_payload(args.output, payload)
        printable = payload if args.full_output else _summary_payload(payload)
        print(json.dumps(printable, indent=2, sort_keys=True))
        if report.passed or args.allow_failures:
            return 0
        return 1
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _te_assets_exist(asset_root: Path, idv: str) -> bool:
    trace_dir = asset_root / "extracted" / idv
    return all(
        (trace_dir / name).exists()
        for name in ("t.dat", "y.dat", "u.dat", "r.dat")
    )


def _summary_payload(payload: dict[str, object]) -> dict[str, object]:
    live_report = payload.get("live_report")
    cases = payload.get("cases")
    summary: dict[str, object] = {
        "backend_source": payload.get("backend_source"),
        "scenario_id": payload.get("scenario_id"),
        "modbus_endpoint": payload.get("modbus_endpoint"),
        "hmi_state_url": payload.get("hmi_state_url"),
        "event_log": payload.get("event_log"),
    }
    if isinstance(cases, list):
        summary["case_count"] = len(cases)
    if isinstance(live_report, Mapping):
        summary["passed"] = live_report.get("passed")
        summary["total_steps"] = live_report.get("total_steps")
        summary["passed_steps"] = live_report.get("passed_steps")
        summary["protocol_status_counts"] = live_report.get(
            "protocol_status_counts"
        )
    return summary


def _write_synthetic_te_trace(trace_dir: Path) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    _write_matrix(trace_dir / "t.dat", [[0.0], [0.1], [0.2], [0.3]])
    _write_matrix(trace_dir / "y.dat", [_row(float(row), 51) for row in range(1, 5)])
    _write_matrix(trace_dir / "u.dat", [_row(float(row), 12) for row in range(10, 14)])
    _write_matrix(trace_dir / "r.dat", [_row(float(row), 36) for row in range(100, 104)])


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
