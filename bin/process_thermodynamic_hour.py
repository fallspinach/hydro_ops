#!/usr/bin/env python3
"""Create one elevation-aware T/P/q/longwave forcing hour on the NWM grid."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.thermodynamic_hour import process_thermodynamic_hour


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--product", required=True, choices=("nldas2", "hrrr"))
    parser.add_argument("--source-elevation", required=True, type=Path)
    parser.add_argument("--source-elevation-variable", required=True)
    parser.add_argument("--target-grid", required=True, type=Path)
    parser.add_argument("--target-elevation", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--final-temperature", type=Path)
    parser.add_argument("--final-temperature-variable", default="T2D")
    parser.add_argument("--cdo", default="cdo")
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--relative-humidity-tolerance", type=float, default=0.10)
    parser.add_argument("--skip-weight-validation", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = process_thermodynamic_hour(
        args.source, args.product, args.source_elevation, args.source_elevation_variable,
        args.target_grid, args.target_elevation, args.weights, args.output,
        final_temperature_path=args.final_temperature,
        final_temperature_variable=args.final_temperature_variable,
        cdo=args.cdo, work_directory=args.work_directory,
        relative_humidity_tolerance=args.relative_humidity_tolerance,
        validate_weights=not args.skip_weight_validation, force=args.force,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
