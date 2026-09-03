#!/usr/bin/env python3
"""Apply stable monthly PRISM constraints to daily NWM forcing archives."""

from __future__ import annotations

import argparse
import calendar
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.config import load_settings
from hydro_ops.forcing.monthly_prism import (
    apply_monthly_precipitation_hour,
    assess_monthly_reconciliation,
    monthly_temperature_adjustment,
    nearest_wet_timing_donors,
    reconcile_prism_month,
)
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.physics import (
    cosgrove_atmospheric_emissivity,
    relative_humidity_from_specific_humidity,
    specific_humidity_from_relative_humidity,
)
from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_temperature import create_prism_temperature_constraints
from hydro_ops.forcing.streams import validate_stream_output_root
from hydro_ops.work import temporary_work_root


def month_days(year: int, month: int) -> list[date]:
    return [date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1)]


def daily_path(root: Path, day: date) -> Path:
    candidates = (
        root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1",
        root / day.strftime("%Y") / f"{day:%Y%m%d}.LDASIN_DOMAIN1",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Missing daily baseline forcing for {day}: {candidates[0]}")


def _finite_sum_and_count(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(values)
    return np.where(valid, values, 0.0), valid.astype(np.uint16)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--month", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--complete-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--stream", choices=("retro",), default="retro")
    parser.add_argument("--precipitation-weights", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--max-iterations", type=int, default=80)
    parser.add_argument("--maximum-unconverged-fraction", type=float, default=0.005)
    parser.add_argument("--maximum-unresolved-fraction", type=float, default=0.005)
    parser.add_argument("--maximum-capped-fraction", type=float, default=0.02)
    parser.add_argument("--maximum-dry-baseline-wet-target-fraction", type=float, default=0.005)
    parser.add_argument("--maximum-synthetic-timing-fraction", type=float, default=0.01)
    parser.add_argument("--maximum-monthly-depth", type=float, default=4000.0)
    parser.add_argument("--maximum-ratio", type=float, default=10.0)
    parser.add_argument("--maximum-corrected-hourly-depth", type=float, default=300.0)
    parser.add_argument(
        "--allow-synthetic-timing",
        action="store_true",
        help="use the nearest wet NWM cell's hourly profile where the monthly baseline is dry",
    )
    parser.add_argument(
        "--diagnostics-only",
        action="store_true",
        help="derive and validate monthly corrections without publishing daily archives",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    validate_stream_output_root(args.output_root, args.stream)
    if args.complete_root.resolve() == args.output_root.resolve():
        parser.error("--output-root must differ from --complete-root")

    settings = load_settings()
    layout = OperationalLayout.project_defaults()
    days = month_days(args.year, args.month)
    inputs = [daily_path(args.complete_root, day) for day in days]
    outputs = [
        args.output_root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1" for day in days
    ]
    existing = [path for path in outputs if path.exists()]
    if existing and not args.force:
        raise FileExistsError(f"{len(existing)} outputs exist; use --force to replace them")

    monthly_root = settings.prism_data_dir.parent / "monthly"
    stamp = f"{args.year:04d}{args.month:02d}"
    prism = {
        variable: monthly_root
        / variable
        / f"{args.year:04d}"
        / f"prism_{variable}_us_25m_{stamp}.nc"
        for variable in ("ppt", "tmin", "tmax")
    }
    for path in prism.values():
        if not path.is_file():
            raise FileNotFoundError(f"Required monthly PRISM input is missing: {path}")

    work_root = args.work_directory or temporary_work_root(settings, f"prism-month-{stamp}")
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"prism_month_{stamp}_", dir=work_root) as temp_name:
        temp = Path(temp_name)
        constraint_path = temp / f"prism_temperature_constraint.{stamp}.nc"
        create_prism_temperature_constraints(
            prism["tmin"],
            prism["tmax"],
            settings.data_root / "static/prism/prism_an_4km_elevation.nc",
            layout.target_grid,
            layout.target_elevation,
            settings.data_root / "static/remapping/nwm_conus_1km/prism_bilinear.nc",
            constraint_path,
            work_directory=temp,
            force=True,
        )

        precipitation_sum = None
        maximum_hourly_depth = None
        minimum_sum = maximum_sum = None
        minimum_count = maximum_count = None
        grid_shape = None
        available_hours = 0
        expected_month_hours = 24 * len(days)
        for day, path in zip(days, inputs, strict=True):
            with Dataset(path) as data:
                records = len(data.dimensions["time"])
                allowed_partial = day == date(1979, 1, 1) and records == 11
                if records != 24 and not allowed_partial:
                    raise ValueError(f"Daily archive has unsupported record count: {path}")
                available_hours += records
                rain = np.ma.asarray(data["RAINRATE"][:], dtype=np.float64).filled(np.nan)
                temperature = np.ma.asarray(data["T2D"][:], dtype=np.float64).filled(np.nan)
            if grid_shape is None:
                grid_shape = rain.shape[1:]
                precipitation_sum = np.zeros(grid_shape, dtype=np.float64)
                maximum_hourly_depth = np.zeros(grid_shape, dtype=np.float64)
                minimum_sum = np.zeros(grid_shape, dtype=np.float64)
                maximum_sum = np.zeros(grid_shape, dtype=np.float64)
                minimum_count = np.zeros(grid_shape, dtype=np.uint16)
                maximum_count = np.zeros(grid_shape, dtype=np.uint16)
            daily_depth = np.nansum(rain * 3600.0, axis=0)
            daily_depth[~np.any(np.isfinite(rain), axis=0)] = np.nan
            precipitation_sum += np.nan_to_num(daily_depth)
            maximum_hourly_depth = np.maximum(
                maximum_hourly_depth,
                np.nanmax(np.where(np.isfinite(rain), rain * 3600.0, 0.0), axis=0),
            )
            if records != 24:
                continue
            finite_temperature = np.isfinite(temperature)
            daily_minimum = np.min(np.where(finite_temperature, temperature, np.inf), axis=0)
            daily_maximum = np.max(np.where(finite_temperature, temperature, -np.inf), axis=0)
            no_temperature = ~np.any(finite_temperature, axis=0)
            daily_minimum[no_temperature] = np.nan
            daily_maximum[no_temperature] = np.nan
            values, count = _finite_sum_and_count(daily_minimum)
            minimum_sum += values
            minimum_count += count
            values, count = _finite_sum_and_count(daily_maximum)
            maximum_sum += values
            maximum_count += count

        assert precipitation_sum is not None and grid_shape is not None
        mean_minimum = np.divide(
            minimum_sum, minimum_count, out=np.full(grid_shape, np.nan), where=minimum_count > 0
        )
        mean_maximum = np.divide(
            maximum_sum, maximum_count, out=np.full(grid_shape, np.nan), where=maximum_count > 0
        )
        with xr.open_dataset(constraint_path, mask_and_scale=True) as constraint:
            prism_minimum = np.asarray(constraint["prism_tmin"].values, dtype=np.float64)
            prism_maximum = np.asarray(constraint["prism_tmax"].values, dtype=np.float64)
        temperature_adjustment = monthly_temperature_adjustment(
            mean_minimum, mean_maximum, prism_minimum, prism_maximum
        )
        with xr.open_dataset(prism["ppt"], mask_and_scale=True) as source:
            prism_depth = np.asarray(source["ppt"].squeeze().values, dtype=np.float64)
        coverage_fraction = available_hours / expected_month_hours
        prism_depth *= coverage_fraction
        precipitation_weights = args.precipitation_weights or (
            settings.data_root
            / "static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc"
        )
        precipitation = reconcile_prism_month(
            precipitation_sum,
            prism_depth,
            ConservativeOperator.from_cdo(precipitation_weights),
            max_iterations=args.max_iterations,
            ratio_bounds=(0.1, args.maximum_ratio),
            cumulative_ratio_bounds=(0.0, args.maximum_ratio),
            maximum_monthly_depth=args.maximum_monthly_depth,
            allow_synthetic_timing=args.allow_synthetic_timing,
        )

        assessment = assess_monthly_reconciliation(
            precipitation,
            prism_depth,
            maximum_unconverged_fraction=args.maximum_unconverged_fraction,
            maximum_unresolved_fraction=args.maximum_unresolved_fraction,
            maximum_capped_fraction=args.maximum_capped_fraction,
            maximum_dry_baseline_wet_target_fraction=(
                args.maximum_dry_baseline_wet_target_fraction
            ),
            maximum_synthetic_timing_fraction=args.maximum_synthetic_timing_fraction,
        )
        synthetic_mask, donor_y, donor_x = nearest_wet_timing_donors(
            precipitation_sum, precipitation.daily_depth
        )
        safe_precipitation_factor = np.where(
            np.isfinite(precipitation.correction_factor),
            precipitation.correction_factor,
            1.0,
        )
        corrected_maximum = maximum_hourly_depth * safe_precipitation_factor
        if np.any(synthetic_mask):
            donor_depth = precipitation_sum[donor_y, donor_x]
            synthetic_maximum = np.divide(
                maximum_hourly_depth[donor_y, donor_x] * precipitation.daily_depth,
                donor_depth,
                out=np.zeros_like(precipitation.daily_depth),
                where=np.isfinite(donor_depth) & (donor_depth > 0),
            )
            corrected_maximum[synthetic_mask] = synthetic_maximum[synthetic_mask]
        maximum_corrected_hourly_depth = float(np.nanmax(corrected_maximum))
        accepted = assessment.accepted and (
            maximum_corrected_hourly_depth <= args.maximum_corrected_hourly_depth
        )
        created = datetime.now(UTC).isoformat()
        diagnostics = (
            args.output_root / f"{args.year:04d}" / f"{stamp}.monthly_prism_diagnostics.nc"
        )
        diagnostic = xr.Dataset(
            {
                "corrected_monthly_depth": (
                    ("y", "x"),
                    precipitation.daily_depth.astype(np.float32),
                ),
                "precipitation_correction_factor": (
                    ("y", "x"),
                    precipitation.correction_factor.astype(np.float32),
                ),
                "temperature_midpoint_shift": (
                    ("y", "x"),
                    temperature_adjustment.midpoint_shift.astype(np.float32),
                ),
                "temperature_range_scale": (
                    ("y", "x"),
                    temperature_adjustment.range_scale.astype(np.float32),
                ),
                "temperature_constraint_valid": (
                    ("y", "x"),
                    temperature_adjustment.constraint_valid.astype(np.uint8),
                ),
                "synthetic_timing_source": (
                    ("y", "x"),
                    synthetic_mask.astype(np.uint8),
                ),
                "prism_target_residual": (
                    ("prism_y", "prism_x"),
                    precipitation.target_residual.astype(np.float32),
                ),
                "prism_reconciliation_qc": (
                    ("prism_y", "prism_x"),
                    precipitation.target_qc_flags,
                ),
            },
            attrs={
                "forcing_stream": args.stream,
                "period": stamp,
                "available_hour_count": available_hours,
                "expected_month_hour_count": expected_month_hours,
                "prism_precipitation_coverage_fraction": coverage_fraction,
                "precipitation_weights": str(precipitation_weights),
                "prism_constraint_frequency": "monthly",
                "precipitation_converged": str(precipitation.converged).lower(),
                "precipitation_accepted": str(accepted).lower(),
                "precipitation_constrained_cells": assessment.constrained_cells,
                "precipitation_unconverged_fraction": assessment.unconverged_fraction,
                "precipitation_unresolved_fraction": assessment.unresolved_fraction,
                "precipitation_capped_fraction": assessment.capped_fraction,
                "precipitation_dry_baseline_wet_target_fraction": (
                    assessment.dry_baseline_wet_target_fraction
                ),
                "precipitation_synthetic_timing_fraction": (
                    assessment.synthetic_timing_fraction
                ),
                "maximum_unconverged_fraction": args.maximum_unconverged_fraction,
                "maximum_unresolved_fraction": args.maximum_unresolved_fraction,
                "maximum_capped_fraction": args.maximum_capped_fraction,
                "maximum_dry_baseline_wet_target_fraction": (
                    args.maximum_dry_baseline_wet_target_fraction
                ),
                "maximum_synthetic_timing_fraction": args.maximum_synthetic_timing_fraction,
                "synthetic_timing_method": "nearest_wet_nwm_monthly_profile",
                "maximum_precipitation_ratio": args.maximum_ratio,
                "maximum_corrected_hourly_depth_mm": maximum_corrected_hourly_depth,
                "maximum_allowed_corrected_hourly_depth_mm": (
                    args.maximum_corrected_hourly_depth
                ),
                "precipitation_iterations": precipitation.iterations,
                "created": created,
            },
        )
        diagnostics.parent.mkdir(parents=True, exist_ok=True)
        diagnostic.to_netcdf(
            diagnostics,
            encoding={name: {"zlib": True, "complevel": 2} for name in diagnostic.data_vars},
        )
        if not accepted:
            print(
                f"REJECTED diagnostics={diagnostics} "
                f"unresolved_fraction={assessment.unresolved_fraction:.6f} "
                f"capped_fraction={assessment.capped_fraction:.6f} "
                "dry_baseline_wet_target_fraction="
                f"{assessment.dry_baseline_wet_target_fraction:.6f} "
                f"maximum_corrected_hourly_depth_mm={maximum_corrected_hourly_depth:.3f}",
                flush=True,
            )
            return 2
        if args.diagnostics_only:
            print(
                f"ACCEPTED diagnostics={diagnostics} "
                f"unresolved_fraction={assessment.unresolved_fraction:.6f}",
                flush=True,
            )
            return 0

        for day, source_path, destination in zip(days, inputs, outputs, strict=True):
            staged = temp / destination.name
            shutil.copyfile(source_path, staged)
            with Dataset(staged, "a") as output:
                for hour in range(len(output.dimensions["time"])):
                    old_temperature = np.ma.asarray(output["T2D"][hour]).filled(np.nan)
                    pressure = np.ma.asarray(output["PSFC"][hour]).filled(np.nan)
                    old_humidity = np.ma.asarray(output["Q2D"][hour]).filled(np.nan)
                    old_longwave = np.ma.asarray(output["LWDOWN"][hour]).filled(np.nan)
                    rainrate = np.ma.asarray(output["RAINRATE"][hour]).filled(np.nan)
                    final_temperature = temperature_adjustment.apply(old_temperature)
                    relative_humidity = relative_humidity_from_specific_humidity(
                        old_humidity, old_temperature, pressure, phase="water"
                    )
                    final_humidity = specific_humidity_from_relative_humidity(
                        relative_humidity, final_temperature, pressure, phase="water"
                    )
                    old_emission = cosgrove_atmospheric_emissivity(
                        old_temperature, old_humidity, pressure
                    ) * np.power(old_temperature, 4)
                    factor = np.divide(
                        old_longwave,
                        old_emission,
                        out=np.full(grid_shape, np.nan),
                        where=np.isfinite(old_emission) & (old_emission > 0),
                    )
                    new_emission = cosgrove_atmospheric_emissivity(
                        final_temperature, final_humidity, pressure
                    ) * np.power(final_temperature, 4)
                    output["T2D"][hour] = np.ma.masked_invalid(final_temperature)
                    output["Q2D"][hour] = np.ma.masked_invalid(final_humidity)
                    output["LWDOWN"][hour] = np.ma.masked_invalid(factor * new_emission)
                    output["RAINRATE"][hour] = np.ma.masked_invalid(
                        apply_monthly_precipitation_hour(
                            rainrate,
                            precipitation_sum,
                            precipitation.daily_depth,
                            precipitation.correction_factor,
                            synthetic_mask,
                            donor_y,
                            donor_x,
                        )
                    )
                output.setncatts(
                    {
                        "forcing_stream": args.stream,
                        "prism_constraint_frequency": "monthly",
                        "prism_constraint_period": stamp,
                        "prism_precipitation_coverage_fraction": coverage_fraction,
                        "available_hour_count": available_hours,
                        "expected_month_hour_count": expected_month_hours,
                        "prism_precipitation_source": str(prism["ppt"]),
                        "prism_temperature_minimum_source": str(prism["tmin"]),
                        "prism_temperature_maximum_source": str(prism["tmax"]),
                        "prism_reconciliation_converged": str(precipitation.converged).lower(),
                        "prism_reconciliation_accepted": str(accepted).lower(),
                        "prism_reconciliation_unconverged_fraction": (
                            assessment.unconverged_fraction
                        ),
                        "prism_reconciliation_unresolved_fraction": (
                            assessment.unresolved_fraction
                        ),
                        "prism_reconciliation_capped_fraction": assessment.capped_fraction,
                        "prism_reconciliation_dry_baseline_wet_target_fraction": (
                            assessment.dry_baseline_wet_target_fraction
                        ),
                        "prism_reconciliation_synthetic_timing_fraction": (
                            assessment.synthetic_timing_fraction
                        ),
                        "prism_synthetic_timing_method": (
                            "nearest_wet_nwm_monthly_profile"
                        ),
                        "prism_maximum_corrected_hourly_depth_mm": (
                            maximum_corrected_hourly_depth
                        ),
                        "monthly_constraint_created": created,
                    }
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(f"{destination.name}.part")
            partial.unlink(missing_ok=True)
            shutil.copyfile(staged, partial)
            partial.replace(destination)
            staged.unlink()

    print(f"published={len(outputs)} diagnostics={diagnostics}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
