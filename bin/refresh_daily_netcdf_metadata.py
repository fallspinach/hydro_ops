#!/usr/bin/env python3
"""Remove stale hourly coverage attributes and refresh extrema in daily NetCDF files."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def refresh(path: Path) -> None:
    with Dataset(path, "r+") as dataset:
        time = dataset.variables.get("time")
        if time is not None:
            for attribute in ("begin_date", "begin_time", "end_date", "end_time"):
                if attribute in time.ncattrs():
                    time.delncattr(attribute)
        for variable in dataset.variables.values():
            extrema = {name for name in ("vmin", "vmax") if name in variable.ncattrs()}
            if not extrema or not np.issubdtype(variable.dtype, np.number):
                continue
            values = np.ma.asarray(variable[...])
            if values.count() == 0:
                continue
            if "vmin" in extrema:
                variable.setncattr("vmin", np.asarray(values.min()).item())
            if "vmax" in extrema:
                variable.setncattr("vmax", np.asarray(values.max()).item())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--pattern", default="*/NLDAS_FORA0125_H.A????????.020.nc")
    args = parser.parse_args()
    for path in sorted(args.root.glob(args.pattern)):
        refresh(path)
        print(f"READY  {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
