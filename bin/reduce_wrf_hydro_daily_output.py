#!/usr/bin/env python3
"""Write a reference daily-resolution WRF-Hydro product from hourly outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.wrf_hydro.daily_output import load_reducers, reduce_hourly_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", required=True, choices=("LDASOUT", "CHRTOUT"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--reducers",
        type=Path,
        default=Path("config/wrf_hydro_daily_reducers.toml"),
    )
    parser.add_argument("hourly", nargs="+", type=Path)
    args = parser.parse_args()
    if len(args.hourly) != 24:
        parser.error("exactly 24 ordered hourly files are required")
    result = reduce_hourly_files(
        sorted(args.hourly), args.product, load_reducers(args.reducers, args.product)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.partial")
    encoding = {
        name: {"zlib": True, "complevel": 2, "shuffle": True}
        for name in result.data_vars
        if result[name].dtype.kind not in {"S", "U", "O"}
    }
    result.to_netcdf(temporary, engine="netcdf4", encoding=encoding)
    result.close()
    temporary.replace(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
