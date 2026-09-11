from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.evaluation import (
    ConsistencyBenchmarkRunner,
    build_default_modbus_consistency_cases,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate and run the local ICS honeypot consistency benchmark."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path. Parent directories are created if needed.",
    )
    parser.add_argument(
        "--cases-only",
        action="store_true",
        help="Only emit benchmark case definitions without running the runtime.",
    )
    args = parser.parse_args()

    cases = build_default_modbus_consistency_cases()
    if args.cases_only:
        payload = {"cases": [case.to_dict() for case in cases]}
    else:
        payload = {
            "cases": [case.to_dict() for case in cases],
            "report": ConsistencyBenchmarkRunner().run(cases).to_dict(),
        }

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
