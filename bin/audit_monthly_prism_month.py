#!/usr/bin/env python3
"""Independently verify one published monthly-PRISM forcing month."""

from __future__ import annotations

import argparse
import calendar
import json
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date


def path(root: Path, day: date) -> Path:
    return root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--retro-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    days = [
        date(args.year, args.month, d)
        for d in range(1, calendar.monthrange(args.year, args.month)[1] + 1)
    ]
    stamp = f"{args.year:04d}{args.month:02d}"
    diagnostic = args.retro_root / f"{args.year:04d}" / f"{stamp}.monthly_prism_diagnostics.nc"
    rain_sum = None
    baseline_min_sum = baseline_max_sum = retro_min_sum = retro_max_sum = None
    temperature_days = 0
    max_rain = 0.0
    records = 0
    metadata_errors: list[str] = []
    for day in days:
        source = path(args.retro_root, day)
        baseline_source = path(args.baseline_root, day)
        if not baseline_source.is_file():
            metadata_errors.append(f"{day}: baseline missing")
            continue
        with Dataset(source) as data:
            n = len(data.dimensions["time"])
            expected = 11 if day == date(1979, 1, 1) else 24
            time = data["time"]
            values = num2date(
                time[:],
                time.units,
                calendar=getattr(time, "calendar", "standard"),
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
            if n != expected or any(value.date() != day for value in values):
                metadata_errors.append(f"{day}: time coverage")
            if (
                str(getattr(data, "prism_constraint_frequency", "")) != "monthly"
                or str(getattr(data, "prism_reconciliation_accepted", "")).lower() != "true"
            ):
                metadata_errors.append(f"{day}: PRISM metadata")
            rain = np.ma.asarray(data["RAINRATE"][:], dtype=np.float64).filled(np.nan) * 3600.0
            retro_temperature = np.ma.asarray(data["T2D"][:], dtype=np.float64).filled(np.nan)
            total = np.nansum(rain, axis=0)
            rain_sum = total if rain_sum is None else rain_sum + total
            max_rain = max(max_rain, float(np.nanmax(rain)))
            records += n
        if n == 24:
            with Dataset(baseline_source) as data:
                baseline_temperature = np.ma.asarray(data["T2D"][:], dtype=np.float64).filled(
                    np.nan
                )
            extrema = (
                np.nanmin(baseline_temperature, axis=0),
                np.nanmax(baseline_temperature, axis=0),
                np.nanmin(retro_temperature, axis=0),
                np.nanmax(retro_temperature, axis=0),
            )
            if baseline_min_sum is None:
                baseline_min_sum, baseline_max_sum, retro_min_sum, retro_max_sum = [
                    np.nan_to_num(value) for value in extrema
                ]
            else:
                for total_array, value in zip(
                    (baseline_min_sum, baseline_max_sum, retro_min_sum, retro_max_sum),
                    extrema,
                    strict=True,
                ):
                    total_array += np.nan_to_num(value)
            temperature_days += 1
    with Dataset(diagnostic) as data:
        target = np.ma.asarray(data["corrected_monthly_depth"][:], dtype=np.float64).filled(np.nan)
        shift = np.ma.asarray(data["temperature_midpoint_shift"][:], dtype=np.float64).filled(
            np.nan
        )
        scale = np.ma.asarray(data["temperature_range_scale"][:], dtype=np.float64).filled(np.nan)
        constraint_valid = np.asarray(data["temperature_constraint_valid"][:], dtype=bool)
        accepted = str(getattr(data, "precipitation_accepted", "")).lower() == "true"
    valid = np.isfinite(target) & np.isfinite(rain_sum)
    difference = np.abs(rain_sum[valid] - target[valid])
    baseline_minimum = baseline_min_sum / temperature_days
    baseline_maximum = baseline_max_sum / temperature_days
    baseline_midpoint = (baseline_minimum + baseline_maximum) / 2.0
    target_midpoint = baseline_midpoint + shift
    expected_minimum = target_midpoint + scale * (baseline_minimum - baseline_midpoint)
    expected_maximum = target_midpoint + scale * (baseline_maximum - baseline_midpoint)
    observed_minimum = retro_min_sum / temperature_days
    observed_maximum = retro_max_sum / temperature_days
    temperature_error = np.maximum(
        np.abs(observed_minimum - expected_minimum),
        np.abs(observed_maximum - expected_maximum),
    )[constraint_valid]
    report = {
        "period": stamp,
        "records": records,
        "diagnostic_accepted": accepted,
        "metadata_errors": metadata_errors,
        "compared_cells": int(valid.sum()),
        "precipitation_max_abs_error_mm": float(difference.max(initial=0.0)),
        "precipitation_rmse_mm": float(np.sqrt(np.mean(difference**2)))
        if difference.size
        else None,
        "maximum_hourly_depth_mm": max_rain,
        "temperature_extrema_max_abs_error_k": float(np.nanmax(temperature_error, initial=0.0)),
    }
    report["passed"] = (
        accepted
        and not metadata_errors
        and report["precipitation_max_abs_error_mm"] <= 0.05
        and report["temperature_extrema_max_abs_error_k"] <= 0.02
        and max_rain <= 300.0
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
