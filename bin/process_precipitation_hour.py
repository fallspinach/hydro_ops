#!/usr/bin/env python3
"""Remap and quality-composite one hourly precipitation field."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.precipitation_hour import process_precipitation_hour


def _mapping(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        try:
            product, path = value.split("=", 1)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"Expected PRODUCT=PATH: {value}") from error
        result[product] = Path(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", default=[], metavar="PRODUCT=PATH")
    parser.add_argument("--weights", action="append", default=[], metavar="PRODUCT=PATH")
    parser.add_argument("--quality", type=Path)
    parser.add_argument("--quality-weights", type=Path)
    parser.add_argument("--stage4-override", type=Path)
    parser.add_argument("--target-grid", type=Path, required=True)
    parser.add_argument("--remap-grid", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mrms-quality-threshold", type=float, default=0.5)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--skip-weight-validation", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = process_precipitation_hour(
        _mapping(args.candidate), _mapping(args.weights), args.target_grid, args.output,
        remap_grid_path=args.remap_grid,
        quality_path=args.quality, quality_weights=args.quality_weights,
        stage4_override_path=args.stage4_override,
        mrms_quality_threshold=args.mrms_quality_threshold,
        work_directory=args.work_directory,
        validate_weights=not args.skip_weight_validation, force=args.force,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
