#!/usr/bin/env python3
"""Preview incremental fast/daily forcing work from explicit source-change events."""
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.forcing.operational_strategy import plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, type=Path, help="JSON array of source-change events")
    parser.add_argument("--as-of", default=datetime.now(UTC).isoformat())
    parser.add_argument("--lane", choices=("fast", "daily-nrt", "daily-retro"), default="fast")
    parser.add_argument("--recent-days", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(plan(json.loads(args.events.read_text()), args.as_of,
                          lane=args.lane, recent_days=args.recent_days), indent=2))


if __name__ == "__main__":
    main()
