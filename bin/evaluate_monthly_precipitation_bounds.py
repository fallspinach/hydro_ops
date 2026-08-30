#!/usr/bin/env python3
"""Evaluate monthly PRISM ratio bounds from an existing aggregate diagnostic."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.forcing.monthly_prism import (
    assess_monthly_reconciliation,
    reconcile_prism_month,
)
from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--prism", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--maximum-ratio", required=True, type=float)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-iterations", type=int, default=80)
    args = parser.parse_args()
    with xr.open_dataset(args.diagnostics, mask_and_scale=True) as diagnostic:
        corrected = np.asarray(diagnostic["corrected_monthly_depth"].values, dtype=np.float64)
        factor = np.asarray(diagnostic["precipitation_correction_factor"].values, dtype=np.float64)
    baseline = np.divide(
        corrected,
        factor,
        out=np.zeros_like(corrected),
        where=np.isfinite(factor) & (factor > 0),
    )
    with xr.open_dataset(args.prism, mask_and_scale=True) as prism:
        target = np.asarray(prism["ppt"].squeeze().values, dtype=np.float64)
    result = reconcile_prism_month(
        baseline,
        target,
        ConservativeOperator.from_cdo(args.weights),
        max_iterations=args.max_iterations,
        ratio_bounds=(0.1, args.maximum_ratio),
        cumulative_ratio_bounds=(0.0, args.maximum_ratio),
    )
    assessment = assess_monthly_reconciliation(result, target)
    finite_factor = result.correction_factor[np.isfinite(result.correction_factor)]
    report = {
        "created": datetime.now(UTC).isoformat(),
        "maximum_ratio": args.maximum_ratio,
        "iterations": result.iterations,
        "solver_converged": result.converged,
        "correction_factor_quantiles": {
            name: float(value)
            for name, value in zip(
                ("minimum", "p50", "p90", "p95", "p99", "p999", "maximum"),
                np.quantile(finite_factor, (0, 0.5, 0.9, 0.95, 0.99, 0.999, 1)),
                strict=True,
            )
        },
        "maximum_corrected_monthly_depth_mm": float(np.nanmax(result.daily_depth)),
        **assessment.__dict__,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(f"{args.output.suffix}.part")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.output)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
