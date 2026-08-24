#!/usr/bin/env python3
"""Batch-remap and composite precipitation for one 12Z-to-12Z forcing day."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.forcing.operations import OperationalLayout, discover_precipitation_candidates
from hydro_ops.forcing.precipitation_day import process_precipitation_day
from hydro_ops.work import temporary_work_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    layout = OperationalLayout.project_defaults()
    start = datetime.combine(args.day - timedelta(days=1), time(12), tzinfo=UTC)
    valid_times = [start + timedelta(hours=hour) for hour in range(24)]
    discovered = [discover_precipitation_candidates(valid, layout) for valid in valid_times]
    candidate_hours = [item[0] for item in discovered]
    quality_hours = [item[1] for item in discovered]
    products = set(candidate_hours[0])
    weights = {
        name: (
            layout.mrms_conservative
            if name.startswith("mrms_")
            else layout.stage4_conservative
            if name.startswith("stage4_")
            else layout.nldas2_conservative
            if name == "nldas2"
            else layout.hrrr_conservative
        )
        for name in products
    }
    outputs = process_precipitation_day(
        valid_times,
        candidate_hours,
        quality_hours,
        weights,
        layout.target_grid,
        layout.remap_grid,
        args.output_directory,
        quality_weights=layout.mrms_quality_bilinear,
        work_directory=temporary_work_root(load_settings(), f"precipitation-day-{args.day:%Y%m%d}"),
        force=args.force,
    )
    print(f"{len(outputs)} hourly precipitation files in {args.output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
