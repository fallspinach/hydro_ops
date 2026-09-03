#!/usr/bin/env python3
"""Report external-source, NWM production, coordinator, and SLURM status."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.status_monitor import build_status, format_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--output", type=Path, help="write atomically to this file instead of stdout"
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, help="production audit start (YYYY-MM-DD)"
    )
    parser.add_argument("--end", type=date.fromisoformat, help="production audit end (YYYY-MM-DD)")
    parser.add_argument("--gap-limit", type=int, default=20)
    parser.add_argument("--no-slurm", action="store_true", help="skip the live scheduler query")
    args = parser.parse_args()
    if args.start and args.end and args.start > args.end:
        parser.error("--start must not be after --end")
    if args.gap_limit < 0:
        parser.error("--gap-limit must be nonnegative")
    report = build_status(
        load_settings(),
        start=args.start,
        end=args.end,
        gap_limit=args.gap_limit,
        include_slurm=not args.no_slurm,
    )
    rendered = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else format_text(report) + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        partial = args.output.with_name(args.output.name + ".part")
        partial.write_text(rendered)
        partial.replace(args.output)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
