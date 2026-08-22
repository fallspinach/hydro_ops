#!/usr/bin/env python3
"""Reconcile 24 hourly LDASIN files to one 12Z-ending PRISM precipitation day."""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    reconcile_prism_day,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hours", nargs=24, type=Path, help="chronological hour-ending LDASIN files")
    parser.add_argument("--prism", required=True, type=Path)
    parser.add_argument("--prism-variable", default="ppt")
    parser.add_argument(
        "--weights", required=True, type=Path, help="conservative NWM-to-PRISM CDO weights"
    )
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--revision", choices=("early", "provisional", "stable"), required=True)
    parser.add_argument("--tolerance", type=float, default=1.0e-3)
    parser.add_argument("--max-iterations", type=int, default=100)
    parser.add_argument("--minimum-ratio", type=float, default=0.1)
    parser.add_argument("--maximum-ratio", type=float, default=10.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    fields = []
    grid_shape = None
    for path in args.hours:
        with Dataset(path) as data:
            if "RAINRATE" not in data.variables or data["RAINRATE"].shape[0] != 1:
                parser.error(f"invalid hourly RAINRATE: {path}")
            field = np.asarray(data["RAINRATE"][0], dtype=np.float64) * 3600.0
            grid_shape = field.shape if grid_shape is None else grid_shape
            if field.shape != grid_shape:
                parser.error("hourly grids differ")
            fields.append(field)
    with xr.open_dataset(args.prism, mask_and_scale=True) as prism_data:
        if args.prism_variable not in prism_data:
            parser.error(f"{args.prism} is missing {args.prism_variable}")
        prism = np.asarray(prism_data[args.prism_variable].squeeze(), dtype=np.float64)
    operator = ConservativeOperator.from_cdo(args.weights)
    result = reconcile_prism_day(
        np.stack(fields),
        prism,
        operator,
        tolerance=args.tolerance,
        max_iterations=args.max_iterations,
        ratio_bounds=(args.minimum_ratio, args.maximum_ratio),
    )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC).isoformat()
    for index, source in enumerate(args.hours):
        destination = args.output_directory / source.name
        if destination.exists() and not args.force:
            raise FileExistsError(f"output exists; use --force to replace it: {destination}")
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        shutil.copy2(source, partial)
        with Dataset(partial, "a") as data:
            data["RAINRATE"][0] = result.hourly_depth[index] / 3600.0
            data.setncattr("prism_precipitation_revision", args.revision)
            data.setncattr("prism_precipitation_source", str(args.prism))
            data.setncattr("prism_reconciliation_converged", str(result.converged).lower())
            history = data.getncattr("history") if "history" in data.ncattrs() else ""
            data.setncattr("history", f"{created} PRISM daily precipitation reconciliation; {history}")
        partial.replace(destination)

    args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_partial = args.diagnostics.with_name(f"{args.diagnostics.stem}.part{args.diagnostics.suffix}")
    diagnostic_partial.unlink(missing_ok=True)
    with Dataset(diagnostic_partial, "w") as data:
        data.createDimension("y", grid_shape[0])
        data.createDimension("x", grid_shape[1])
        data.createDimension("prism_y", prism.shape[-2])
        data.createDimension("prism_x", prism.shape[-1])
        data.createVariable("corrected_daily_depth", "f4", ("y", "x"), zlib=True)[:] = result.daily_depth
        data.createVariable("prism_correction_factor", "f4", ("y", "x"), zlib=True)[:] = (
            result.correction_factor
        )
        data.createVariable("prism_target_residual", "f4", ("prism_y", "prism_x"), zlib=True)[:] = (
            result.target_residual
        )
        data.createVariable("prism_reconciliation_qc", "u2", ("prism_y", "prism_x"), zlib=True)[:] = (
            result.target_qc_flags
        )
        data.setncattr("prism_revision", args.revision)
        data.setncattr("prism_source", str(args.prism))
        data.setncattr("weights", str(args.weights))
        data.setncattr("iterations", result.iterations)
        data.setncattr("converged", str(result.converged).lower())
        data.setncattr("created", created)
    diagnostic_partial.replace(args.diagnostics)
    print(f"{args.diagnostics} converged={result.converged} iterations={result.iterations}")
    return 0 if result.converged else 2


if __name__ == "__main__":
    raise SystemExit(main())
