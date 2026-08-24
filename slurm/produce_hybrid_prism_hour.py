#!/usr/bin/env python3
#SBATCH --job-name=hybrid-prism-hour
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
"""Produce one preliminary hybrid hour in a PRISM-day Slurm array."""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

requested_python = os.environ.get("HYDRO_OPS_PYTHON")
if requested_python and Path(sys.executable).resolve() != Path(requested_python).resolve():
    os.execv(requested_python, [requested_python, __file__, *sys.argv[1:]])

from hydro_ops.config import load_settings
from hydro_ops.forcing.hybrid import HybridWeights
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.produce import produce_seven_field_hour
from hydro_ops.work import temporary_work_root


def main() -> int:
    day = date.fromisoformat(os.environ["HYDRO_OPS_PRISM_DAY"])
    start = datetime.combine(day - timedelta(days=1), time(12), tzinfo=UTC)
    valid = start + timedelta(hours=int(os.environ["SLURM_ARRAY_TASK_ID"]))
    root = Path(os.environ.get("HYDRO_OPS_PROJECT_ROOT", ".")).resolve()
    output_root = Path(os.environ["HYDRO_OPS_PRELIMINARY_ROOT"])
    layout = OperationalLayout.project_defaults(root)
    weight = float(os.environ.get("HYDRO_OPS_HYBRID_TEST_WEIGHT", "0.25"))
    weights = HybridWeights(
        temperature=weight,
        log_pressure=weight,
        relative_humidity=weight,
        log_longwave_factor=weight,
        clear_sky_index=weight,
        wind_u=weight,
        wind_v=weight,
    )
    output = output_root / f"{valid:%Y%m%d%H}.LDASIN_DOMAIN1"
    produce_seven_field_hour(
        valid,
        layout.nldas2_root,
        layout.hrrr_root,
        layout.target_grid,
        layout.target_elevation,
        layout.nldas2_elevation,
        layout.hrrr_elevation,
        layout.nldas2_bilinear,
        layout.hrrr_bilinear,
        output,
        hybrid_weights=weights,
        work_directory=temporary_work_root(load_settings(), "hybrid-prism"),
        force=True,
    )
    print(output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
