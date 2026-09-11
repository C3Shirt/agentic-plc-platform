from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.evaluation import (
    CICModbusImportOptions,
    ConsistencyBenchmarkRunner,
    import_cic_modbus_benchmark,
    recommended_tshark_command,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import CIC Modbus 2023 or tshark-exported Modbus CSV rows into "
            "the local ICS honeypot consistency benchmark schema."
        )
    )
    parser.add_argument(
        "--packets",
        type=Path,
        help=(
            "CSV containing Modbus packet/request fields. Use --print-tshark "
            "to see the recommended PCAP-to-CSV extraction command."
        ),
    )
    parser.add_argument(
        "--attack-log",
        type=Path,
        help=(
            "Optional CIC attack-log CSV. Labels are joined by TransactionID "
            "when available and preserved as event metadata."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path. Parent directories are created if needed.",
    )
    parser.add_argument("--case-id", default="cic_modbus_import")
    parser.add_argument(
        "--description",
        help="Optional benchmark case description.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        help="Import at most this many request rows after response filtering.",
    )
    parser.add_argument(
        "--include-responses",
        action="store_true",
        help="Do not filter rows marked as Modbus responses.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Also run the imported protocol-FSM benchmark case locally.",
    )
    parser.add_argument(
        "--print-tshark",
        metavar=("PCAP", "CSV"),
        nargs=2,
        help="Print a recommended tshark command and exit.",
    )
    args = parser.parse_args()

    if args.print_tshark is not None:
        pcap_path, output_csv = args.print_tshark
        print(recommended_tshark_command(pcap_path, output_csv))
        return 0

    if args.packets is None:
        parser.error("--packets is required unless --print-tshark is used")

    case = import_cic_modbus_benchmark(
        args.packets,
        attack_log_csv=args.attack_log,
        options=CICModbusImportOptions(
            case_id=args.case_id,
            description=args.description,
            max_rows=args.max_rows,
            include_responses=args.include_responses,
        ),
    )
    payload = {"cases": [case.to_dict()]}
    if args.run:
        payload["report"] = ConsistencyBenchmarkRunner().run((case,)).to_dict()

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
