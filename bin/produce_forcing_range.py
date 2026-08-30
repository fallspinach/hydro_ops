#!/usr/bin/env python3
"""Resumably produce complete NWM forcing over an inclusive UTC-hour range."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.hybrid import HybridWeights
from hydro_ops.forcing.operations import OperationalLayout, produce_complete_hour
from hydro_ops.forcing.streams import baseline_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYYMMDDHH")
    parser.add_argument("--end", required=True, help="YYYYMMDDHH")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--work-directory", type=Path, default=Path("work/forcing_production"))
    parser.add_argument("--final-temperature", type=Path)
    parser.add_argument("--mrms-quality-threshold", type=float, default=0.5)
    parser.add_argument("--hybrid-temperature-weight", type=float, default=0.0)
    parser.add_argument("--hybrid-pressure-weight", type=float, default=0.0)
    parser.add_argument("--hybrid-humidity-weight", type=float, default=0.0)
    parser.add_argument("--hybrid-longwave-weight", type=float, default=0.0)
    parser.add_argument("--hybrid-shortwave-weight", type=float, default=0.0)
    parser.add_argument("--hybrid-wind-weight", type=float, default=0.0)
    parser.add_argument("--hybrid-window-cells", type=int, default=33)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y%m%d%H").replace(tzinfo=UTC)
    if start > end:
        parser.error("--start must not be after --end")
    layout = OperationalLayout.project_defaults(args.project_root)
    output_root = args.output_root or baseline_root(args.project_root.resolve())
    hybrid_weights = HybridWeights(
        temperature=args.hybrid_temperature_weight,
        log_pressure=args.hybrid_pressure_weight,
        relative_humidity=args.hybrid_humidity_weight,
        log_longwave_factor=args.hybrid_longwave_weight,
        clear_sky_index=args.hybrid_shortwave_weight,
        wind_u=args.hybrid_wind_weight,
        wind_v=args.hybrid_wind_weight,
    )
    failed = 0
    current = start
    while current <= end:
        output = output_root / current.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        try:
            summary = produce_complete_hour(
                current, layout, output, work_directory=args.work_directory,
                final_temperature=args.final_temperature,
                mrms_quality_threshold=args.mrms_quality_threshold, force=args.force,
                hybrid_weights=hybrid_weights,
                hybrid_window_cells=args.hybrid_window_cells,
            )
        except Exception as error:
            if not args.continue_on_error:
                raise
            failed += 1
            summary = {"valid_time": current.isoformat(), "status": "failed", "error": str(error)}
        print(json.dumps(summary, sort_keys=True), flush=True)
        current += timedelta(hours=1)
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
