#!/usr/bin/env python3
"""Create one solar-checked shortwave and earth-relative wind hour on the NWM grid."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.radiation_wind_hour import process_radiation_wind_hour


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--product", required=True, choices=("nldas2", "hrrr"))
    parser.add_argument("--target-grid", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cdo", default="cdo")
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--shortwave-negative-tolerance", type=float, default=0.1)
    parser.add_argument("--solar-elevation-tolerance-degrees", type=float, default=-0.833)
    parser.add_argument("--maximum-shortwave", type=float, default=1400.0)
    parser.add_argument("--skip-weight-validation", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = process_radiation_wind_hour(
        args.source, args.product, args.target_grid, args.weights, args.output,
        cdo=args.cdo, work_directory=args.work_directory,
        shortwave_negative_tolerance=args.shortwave_negative_tolerance,
        solar_elevation_tolerance_degrees=args.solar_elevation_tolerance_degrees,
        maximum_shortwave=args.maximum_shortwave,
        validate_weights=not args.skip_weight_validation, force=args.force,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
