#!/usr/bin/env python3
"""Create a model-interval daily forcing summary from UTC calendar chunks."""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing.model_interval import load_forcing_reducers, reduce_model_interval_forcing


def forcing_path(root: Path, day: date) -> Path:
    candidates = (
        root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1",
        root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1.nc",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(candidates[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reducers", type=Path, default=Path("config/forcing_daily_reducers.toml")
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        raise FileExistsError(f"Output exists; use --force to replace it: {args.output}")

    reducers, names, units = load_forcing_reducers(args.reducers)
    paths = [forcing_path(args.input_root, args.day), forcing_path(args.input_root, args.day + timedelta(days=1))]
    result = reduce_model_interval_forcing(paths, args.day, reducers, output_names=names, output_units=units)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    encoding = {name: {"zlib": True, "complevel": 2, "shuffle": True} for name in result.data_vars if name != "time_bounds"}
    try:
        result.to_netcdf(temporary, engine="netcdf4", encoding=encoding)
        temporary.replace(args.output)
    finally:
        result.close()
        temporary.unlink(missing_ok=True)
    print(args.output)


if __name__ == "__main__":
    main()
