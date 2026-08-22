#!/usr/bin/env python3
"""Sample GMTED2010 onto the PRISM 4-km grid."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.elevation import create_regular_grid_elevation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dem", type=Path)
    parser.add_argument("prism_grid", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    create_regular_grid_elevation(args.dem, args.prism_grid, args.output, force=args.force)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
