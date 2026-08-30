#!/usr/bin/env python3
"""Evaluate monthly PRISM reconciliation from an NLDAS-2-only monthly baseline."""

from __future__ import annotations

import argparse
import calendar
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.forcing.monthly_prism import (
    assess_monthly_reconciliation,
    reconcile_prism_month,
)
from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.thermodynamic_hour import build_remap_command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--month", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--nldas-root", required=True, type=Path)
    parser.add_argument("--prism", required=True, type=Path)
    parser.add_argument("--target-grid", required=True, type=Path)
    parser.add_argument("--forward-weights", required=True, type=Path)
    parser.add_argument("--reverse-weights", required=True, type=Path)
    parser.add_argument("--maximum-ratio", action="append", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-directory", type=Path)
    args = parser.parse_args()
    days = calendar.monthrange(args.year, args.month)[1]
    paths = [
        args.nldas_root
        / f"{args.year:04d}"
        / f"NLDAS_FORA0125_H.A{args.year:04d}{args.month:02d}{day:02d}.020.nc"
        for day in range(1, days + 1)
    ]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} NLDAS daily archives; first: {missing[0]}")
    accumulated = None
    dimensions = None
    coordinates = None
    for path in paths:
        with xr.open_dataset(path, mask_and_scale=True) as data:
            rain = np.asarray(data["Rainf"].values, dtype=np.float64)
            daily = np.nansum(rain, axis=0)
            daily[~np.any(np.isfinite(rain), axis=0)] = np.nan
            accumulated = daily if accumulated is None else accumulated + daily
            if dimensions is None:
                field = data["Rainf"].isel(time=0)
                dimensions = field.dims
                coordinates = {name: data.coords[name].load() for name in dimensions}
    assert accumulated is not None and dimensions is not None and coordinates is not None
    cdo = shutil.which("cdo")
    if cdo is None:
        raise RuntimeError("CDO executable not found")
    work = args.work_directory or args.output.parent
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nldas_monthly_prism_", dir=work) as temporary:
        native = Path(temporary) / "nldas_monthly.nc"
        remapped = Path(temporary) / "nldas_monthly_nwm.nc"
        xr.Dataset(
            {"precipitation_depth": (dimensions, accumulated.astype(np.float32))},
            coords=coordinates,
        ).to_netcdf(native)
        subprocess.run(
            build_remap_command(cdo, args.target_grid, args.forward_weights, native, remapped),
            check=True,
        )
        with xr.open_dataset(remapped, mask_and_scale=True) as data:
            baseline = np.asarray(data["precipitation_depth"].squeeze().values, dtype=np.float64)
    with xr.open_dataset(args.prism, mask_and_scale=True) as data:
        target = np.asarray(data["ppt"].squeeze().values, dtype=np.float64)
    operator = ConservativeOperator.from_cdo(args.reverse_weights)
    results = []
    for maximum_ratio in args.maximum_ratio:
        result = reconcile_prism_month(
            baseline,
            target,
            operator,
            ratio_bounds=(0.1, maximum_ratio),
            cumulative_ratio_bounds=(0.0, maximum_ratio),
        )
        assessment = assess_monthly_reconciliation(result, target)
        factor = result.correction_factor[np.isfinite(result.correction_factor)]
        results.append(
            {
                "maximum_ratio": maximum_ratio,
                "iterations": result.iterations,
                "solver_converged": result.converged,
                "factor_p50": float(np.quantile(factor, 0.5)),
                "factor_p95": float(np.quantile(factor, 0.95)),
                "factor_p99": float(np.quantile(factor, 0.99)),
                "factor_p999": float(np.quantile(factor, 0.999)),
                "factor_maximum": float(np.max(factor)),
                **assessment.__dict__,
            }
        )
    report = {
        "created": datetime.now(UTC).isoformat(),
        "period": f"{args.year:04d}{args.month:02d}",
        "baseline": "NLDAS-2 only",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(f"{args.output.suffix}.part")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.output)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
