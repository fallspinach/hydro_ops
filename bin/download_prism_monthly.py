#!/usr/bin/env python3
"""Download historical PRISM AN 4-km monthly grids."""

from __future__ import annotations

import argparse

from hydro_ops.config import load_settings
from hydro_ops.download.prism import VARIABLES, download_monthly_year


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", required=True, type=int)
    parser.add_argument("--end-year", required=True, type=int)
    parser.add_argument(
        "--variable", dest="variables", action="append", choices=tuple(VARIABLES), required=True
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.end_year < args.start_year:
        parser.error("--end-year must not precede --start-year")
    settings = load_settings()
    count = 0
    for year in range(args.start_year, args.end_year + 1):
        paths = download_monthly_year(settings, year, tuple(args.variables), force=args.force)
        count += len(paths)
        print(f"{year}: {len(paths)} monthly grids", flush=True)
    print(f"published={count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
