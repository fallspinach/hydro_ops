#!/usr/bin/env python3
"""Sample a geographic DEM onto the NWM 1-km forcing-grid cell centers."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.elevation import create_target_elevation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dem", type=Path)
    parser.add_argument("target_grid", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    create_target_elevation(args.dem, args.target_grid, args.output, force=args.force)
    print(f"Created {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
