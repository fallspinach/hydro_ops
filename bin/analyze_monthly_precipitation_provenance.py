#!/usr/bin/env python3
"""Trace failed monthly PRISM targets into hourly NWM precipitation provenance."""

from __future__ import annotations

import argparse
import calendar
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation import SOURCE_IDS
from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    ReconciliationQC,
)


def _daily_path(root: Path, day: date) -> Path:
    for path in (
        root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1",
        root / day.strftime("%Y") / f"{day:%Y%m%d}.LDASIN_DOMAIN1",
    ):
        if path.is_file():
            return path
    raise FileNotFoundError(f"Missing daily forcing archive for {day}")


def _quantiles(values: np.ndarray) -> dict[str, float]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {}
    return {
        name: float(value)
        for name, value in zip(
            ("minimum", "p01", "p10", "median", "p90", "p99", "maximum"),
            np.quantile(finite, (0, 0.01, 0.1, 0.5, 0.9, 0.99, 1)),
            strict=True,
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--month", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--complete-root", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--prism", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    operator = ConservativeOperator.from_cdo(args.weights)
    with xr.open_dataset(args.diagnostics, mask_and_scale=True) as diagnostic:
        flags = np.asarray(diagnostic["prism_reconciliation_qc"].values, dtype=np.uint16)
        residual = np.asarray(diagnostic["prism_target_residual"].values, dtype=np.float64)
        corrected = np.asarray(diagnostic["corrected_monthly_depth"].values, dtype=np.float64)
        factor = np.asarray(diagnostic["precipitation_correction_factor"].values, dtype=np.float64)
    with xr.open_dataset(args.prism, mask_and_scale=True) as prism:
        target = np.asarray(prism["ppt"].squeeze().values, dtype=np.float64)
    constrained = np.isfinite(target) & ((flags & np.uint16(ReconciliationQC.PRISM_MISSING)) == 0)
    capped = constrained & ((flags & np.uint16(ReconciliationQC.RATIO_CAPPED)) != 0)
    dry_wet = constrained & ((flags & np.uint16(ReconciliationQC.BASE_DRY_TARGET_WET)) != 0)
    relative_error = np.divide(
        np.abs(residual),
        np.maximum(target, 1.0),
        out=np.full_like(residual, np.inf),
        where=constrained & np.isfinite(residual),
    )
    unresolved = constrained & (relative_error > 1.0e-3)
    clean = constrained & ~unresolved & ~capped & ~dry_wet
    source_masks = {
        "capped_linked": operator.backproject_ratio(capped.astype(np.float64), unmapped_value=0.0)
        > 0.0,
        "dry_wet_linked": operator.backproject_ratio(dry_wet.astype(np.float64), unmapped_value=0.0)
        > 0.0,
        "clean_linked": operator.backproject_ratio(clean.astype(np.float64), unmapped_value=0.0)
        > 0.0,
    }
    source_shape = corrected.shape
    source_masks = {name: mask.reshape(source_shape) for name, mask in source_masks.items()}
    baseline = np.divide(
        corrected,
        factor,
        out=np.full(source_shape, np.nan),
        where=np.isfinite(factor) & (factor > 0),
    )

    source_names = {value: key for key, value in SOURCE_IDS.items()}
    totals: dict[str, dict[int, dict[str, float]]] = {
        region: defaultdict(lambda: defaultdict(float)) for region in source_masks
    }
    qc_counts: dict[str, dict[int, int]] = {region: defaultdict(int) for region in source_masks}
    days = [
        date(args.year, args.month, value)
        for value in range(1, calendar.monthrange(args.year, args.month)[1] + 1)
    ]
    for day in days:
        path = _daily_path(args.complete_root, day)
        with Dataset(path) as data:
            for hour in range(24):
                source = np.asarray(data["precip_source_id"][hour], dtype=np.uint8)
                rain = np.ma.asarray(data["RAINRATE"][hour], dtype=np.float64).filled(np.nan)
                qc = np.asarray(data["precip_qc_flags"][hour], dtype=np.uint16)
                for region, mask in source_masks.items():
                    ids = source[mask]
                    depths = rain[mask] * 3600.0
                    for source_id in np.unique(ids):
                        selected = ids == source_id
                        record = totals[region][int(source_id)]
                        record["cell_hours"] += int(np.count_nonzero(selected))
                        record["wet_cell_hours"] += int(
                            np.count_nonzero(selected & np.isfinite(depths) & (depths > 0))
                        )
                        record["depth_mm"] += float(np.nansum(depths[selected]))
                    region_qc = qc[mask]
                    for bit in (1, 2, 4, 8, 16, 32):
                        qc_counts[region][bit] += int(np.count_nonzero(region_qc & bit))

    report = {
        "created": datetime.now(UTC).isoformat(),
        "period": f"{args.year:04d}{args.month:02d}",
        "inputs": {
            "complete_root": str(args.complete_root),
            "diagnostics": str(args.diagnostics),
            "prism": str(args.prism),
            "weights": str(args.weights),
        },
        "target_cells": {
            "constrained": int(np.count_nonzero(constrained)),
            "capped": int(np.count_nonzero(capped)),
            "dry_baseline_wet_target": int(np.count_nonzero(dry_wet)),
            "unresolved": int(np.count_nonzero(unresolved)),
            "clean": int(np.count_nonzero(clean)),
        },
        "source_regions": {},
    }
    for region, mask in source_masks.items():
        report["source_regions"][region] = {
            "source_cells": int(np.count_nonzero(mask)),
            "baseline_monthly_depth_mm": _quantiles(baseline[mask]),
            "hourly_sources": {
                source_names.get(source_id, f"unknown_{source_id}"): dict(values)
                for source_id, values in sorted(totals[region].items())
            },
            "qc_bit_cell_hours": {
                str(bit): count for bit, count in sorted(qc_counts[region].items())
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(f"{args.output.suffix}.part")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.output)
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
