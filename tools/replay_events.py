from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_plc.telemetry import JsonlEventStore, summarize_events


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay and summarize ICSEvent JSONL.")
    parser.add_argument("event_log", type=Path)
    args = parser.parse_args()

    store = JsonlEventStore(args.event_log)
    summary = summarize_events(store.iter_events())
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
