#!/usr/bin/env python3
"""Produce one UTC day of complete NWM forcing with daily-batched remapping."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from hydro_ops.forcing.complete_day import produce_complete_day
from hydro_ops.forcing.operations import OperationalLayout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/forcing/nwm"))
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--assembly-workers", type=int, default=4)
    parser.add_argument("--precipitation-remap-workers", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    summaries = produce_complete_day(
        args.day,
        OperationalLayout.project_defaults(args.project_root),
        args.output_root,
        work_directory=args.work_directory,
        assembly_workers=args.assembly_workers,
        precipitation_remap_workers=args.precipitation_remap_workers,
        force=args.force,
    )
    for summary in summaries:
        print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
