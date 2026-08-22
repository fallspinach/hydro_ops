#!/usr/bin/env python3
"""Select a source and produce one seven-field NWM forcing hour."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.forcing.produce import produce_seven_field_hour


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("valid_time", help="UTC hour as YYYYMMDDHH")
    parser.add_argument("--nldas2-root", type=Path, required=True)
    parser.add_argument("--hrrr-root", type=Path, required=True)
    parser.add_argument("--target-grid", type=Path, required=True)
    parser.add_argument("--target-elevation", type=Path, required=True)
    parser.add_argument("--nldas2-elevation", type=Path, required=True)
    parser.add_argument("--hrrr-elevation", type=Path, required=True)
    parser.add_argument("--nldas2-weights", type=Path, required=True)
    parser.add_argument("--hrrr-weights", type=Path, required=True)
    parser.add_argument("--final-temperature", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    valid_time = datetime.strptime(args.valid_time, "%Y%m%d%H").replace(tzinfo=UTC)
    output, selected = produce_seven_field_hour(
        valid_time, args.nldas2_root, args.hrrr_root, args.target_grid,
        args.target_elevation, args.nldas2_elevation, args.hrrr_elevation,
        args.nldas2_weights, args.hrrr_weights, args.output,
        final_temperature=args.final_temperature, work_directory=args.work_directory,
        force=args.force,
    )
    print(f"{output} source={selected.product} fallback={selected.fallback_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
