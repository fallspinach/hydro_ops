#!/usr/bin/env python3
"""Apply a PRISM Tmin/Tmax constraint to 24 preliminary hourly temperature files."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.prism_temperature import apply_daily_temperature_constraint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", nargs=24, type=Path)
    parser.add_argument("--constraint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = apply_daily_temperature_constraint(
        args.baseline, args.constraint, args.output, force=args.force
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
