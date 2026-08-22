#!/usr/bin/env python3
"""Download one HRRR native surface-height field and its grid description."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.hrrr_static import download_hrrr_static


def parse_cycle(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%d%H").replace(tzinfo=UTC)
    except ValueError as error:
        raise argparse.ArgumentTypeError("cycle must have format YYYYMMDDHH") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", required=True, type=parse_cycle)
    parser.add_argument("--output-dir", type=Path, default=Path("data/static/hrrr/conus"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    outputs = download_hrrr_static(
        load_settings(), args.cycle, args.output_dir, force=args.force
    )
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
