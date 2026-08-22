#!/usr/bin/env python3
"""Resumably produce complete NWM forcing over an inclusive UTC-hour range."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.operations import OperationalLayout, produce_complete_hour


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYYMMDDHH")
    parser.add_argument("--end", required=True, help="YYYYMMDDHH")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/forcing/nwm"))
    parser.add_argument("--work-directory", type=Path, default=Path("work/forcing_production"))
    parser.add_argument("--final-temperature", type=Path)
    parser.add_argument("--mrms-quality-threshold", type=float, default=0.5)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    if start > end:
        parser.error("--start must not be after --end")
    layout = OperationalLayout.project_defaults(args.project_root)
    failed = 0
    current = start
    while current <= end:
        output = args.output_root / current.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        try:
            summary = produce_complete_hour(
                current, layout, output, work_directory=args.work_directory,
                final_temperature=args.final_temperature,
                mrms_quality_threshold=args.mrms_quality_threshold, force=args.force,
            )
        except Exception as error:
            if not args.continue_on_error:
                raise
            failed += 1
            summary = {"valid_time": current.isoformat(), "status": "failed", "error": str(error)}
        print(json.dumps(summary, sort_keys=True), flush=True)
        current += timedelta(hours=1)
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
