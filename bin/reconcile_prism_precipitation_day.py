#!/usr/bin/env python3
"""Reconcile 24 hourly LDASIN files to one 12Z-ending PRISM precipitation day."""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    ReconciliationQC,
    reconcile_prism_day,
)
from hydro_ops.forcing.prism_temperature import build_constrained_temperature_overrides


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hours", nargs=24, type=Path, help="chronological hour-ending LDASIN files")
    parser.add_argument(
        "--hour-indices", nargs=24, type=int, metavar="INDEX",
        help="time-record index for each input (default: record zero)",
    )
    parser.add_argument("--prism", required=True, type=Path)
    parser.add_argument("--prism-variable", default="ppt")
    parser.add_argument(
        "--weights", required=True, type=Path, help="conservative NWM-to-PRISM CDO weights"
    )
    outputs = parser.add_mutually_exclusive_group(required=True)
    outputs.add_argument("--output-directory", type=Path)
    outputs.add_argument("--daily-output", type=Path)
    parser.add_argument("--day", type=date.fromisoformat, help="PRISM day for --daily-output")
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument(
        "--temperature-corrected-day",
        type=Path,
        help="24-hour corrected T2D file; reconstruct Q2D/LWDOWN and publish all constraints",
    )
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--revision", choices=("early", "provisional", "stable"), required=True)
    parser.add_argument("--tolerance", type=float, default=1.0e-3)
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument("--minimum-ratio", type=float, default=0.1)
    parser.add_argument("--maximum-ratio", type=float, default=10.0)
    parser.add_argument("--cumulative-maximum-ratio", type=float, default=10.0)
    parser.add_argument("--maximum-daily-depth", type=float, default=500.0)
    parser.add_argument("--damping", type=float, default=1.0)
    parser.add_argument("--allow-synthetic-timing", action="store_true")
    parser.add_argument("--maximum-unconverged-fraction", type=float, default=0.005)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    hour_indices = [0] * len(args.hours) if args.hour_indices is None else args.hour_indices
    if not 0 <= args.maximum_unconverged_fraction <= 1:
        parser.error("--maximum-unconverged-fraction must be between zero and one")

    fields = []
    grid_shape = None
    for path, source_index in zip(args.hours, hour_indices, strict=True):
        with Dataset(path) as data:
            if (
                "RAINRATE" not in data.variables
                or not 0 <= source_index < data["RAINRATE"].shape[0]
            ):
                parser.error(f"invalid RAINRATE record {source_index}: {path}")
            field = (
                np.ma.asarray(data["RAINRATE"][source_index], dtype=np.float64).filled(np.nan)
                * 3600.0
            )
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
        cumulative_ratio_bounds=(0.0, args.cumulative_maximum_ratio),
        maximum_daily_depth=args.maximum_daily_depth,
        damping=args.damping,
        allow_synthetic_timing=args.allow_synthetic_timing,
    )
    constrained = (result.target_qc_flags & np.uint16(ReconciliationQC.PRISM_MISSING)) == 0
    unconverged = (
        result.target_qc_flags & np.uint16(ReconciliationQC.NOT_CONVERGED)
    ) != 0
    unconverged_fraction = float(
        np.count_nonzero(unconverged & constrained) / max(np.count_nonzero(constrained), 1)
    )
    accepted = result.converged or unconverged_fraction <= args.maximum_unconverged_fraction
    created = datetime.now(UTC).isoformat()
    if args.daily_output is not None:
        if args.day is None:
            parser.error("--day is required with --daily-output")
        overrides = {"RAINRATE": result.hourly_depth / 3600.0}
        if args.temperature_corrected_day is not None:
            overrides.update(
                build_constrained_temperature_overrides(
                    args.hours, args.temperature_corrected_day, hour_indices
                )
            )
        create_daily_archive(
            args.hours,
            args.daily_output,
            args.day,
            compression_level=2,
            work_directory=args.work_directory,
            time_variable_overrides=overrides,
            global_attributes={
                "prism_day": args.day.isoformat(),
                "forcing_window": "[D-1 12:00 UTC, D 12:00 UTC)",
                "prism_precipitation_revision": args.revision,
                "prism_precipitation_source": str(args.prism),
                "prism_reconciliation_converged": str(result.converged).lower(),
                "prism_reconciliation_accepted": str(accepted).lower(),
                "temperature_constraint_applied": (
                    "yes" if args.temperature_corrected_day is not None else "no"
                ),
                "prism_temperature_corrected_day": (
                    "none"
                    if args.temperature_corrected_day is None
                    else str(args.temperature_corrected_day)
                ),
            },
            verification="targeted",
            fully_verified_overrides={"RAINRATE"},
            source_time_indices=hour_indices,
        )
    else:
        if args.temperature_corrected_day is not None:
            parser.error("--temperature-corrected-day requires --daily-output")
        assert args.output_directory is not None
        args.output_directory.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(args.hours):
            destination = args.output_directory / source.name
            if destination.exists() and not args.force:
                raise FileExistsError(f"output exists; use --force to replace it: {destination}")
            partial = destination.with_name(f"{destination.name}.part")
            partial.unlink(missing_ok=True)
            shutil.copy2(source, partial)
            with Dataset(partial, "a") as data:
                data["RAINRATE"][0] = np.ma.masked_invalid(
                    result.hourly_depth[index] / 3600.0
                )
                data.setncattr("prism_precipitation_revision", args.revision)
                data.setncattr("prism_precipitation_source", str(args.prism))
                data.setncattr("prism_reconciliation_converged", str(result.converged).lower())
                history = data.getncattr("history") if "history" in data.ncattrs() else ""
                data.setncattr(
                    "history", f"{created} PRISM daily precipitation reconciliation; {history}"
                )
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
        data.setncattr("accepted", str(accepted).lower())
        data.setncattr("unconverged_fraction", unconverged_fraction)
        data.setncattr("maximum_unconverged_fraction", args.maximum_unconverged_fraction)
        data.setncattr("maximum_iterations", args.max_iterations)
        data.setncattr("cumulative_maximum_ratio", args.cumulative_maximum_ratio)
        data.setncattr("maximum_daily_depth", args.maximum_daily_depth)
        data.setncattr("damping", args.damping)
        data.setncattr("synthetic_timing", str(args.allow_synthetic_timing).lower())
        data.setncattr("created", created)
    diagnostic_partial.replace(args.diagnostics)
    print(
        f"{args.diagnostics} converged={result.converged} accepted={accepted} "
        f"unconverged_fraction={unconverged_fraction:.6f} iterations={result.iterations}"
    )
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
