#!/usr/bin/env python3
"""Create a WRF-Hydro namelist variant with lake and reservoir routing disabled."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.nwm_config import create_no_lake_namelist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(create_no_lake_namelist(args.source, args.destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
