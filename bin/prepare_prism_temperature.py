#!/usr/bin/env python3
"""Create elevation-aware NWM-grid PRISM Tmin/Tmax constraints."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.prism_temperature import create_prism_temperature_constraints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum", type=Path, required=True)
    parser.add_argument("--maximum", type=Path, required=True)
    parser.add_argument("--source-elevation", type=Path, required=True)
    parser.add_argument("--target-grid", type=Path, required=True)
    parser.add_argument("--target-elevation", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--skip-weight-validation", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = create_prism_temperature_constraints(
        args.minimum, args.maximum, args.source_elevation, args.target_grid,
        args.target_elevation, args.weights, args.output,
        work_directory=args.work_directory,
        validate_weights=not args.skip_weight_validation, force=args.force,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
