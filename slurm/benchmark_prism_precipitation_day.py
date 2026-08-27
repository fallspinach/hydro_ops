#!/usr/bin/env python3
"""Run one full-grid PRISM precipitation reconciliation benchmark."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path


def main() -> int:
    day = date.fromisoformat(os.environ["HYDRO_OPS_PRISM_DAY"])
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    input_root = Path(os.environ["HYDRO_OPS_COMPLETE_ROOT"])
    output_root = Path(os.environ["HYDRO_OPS_OUTPUT_ROOT"])
    start = datetime.combine(day - timedelta(days=1), time(12), tzinfo=UTC)
    hours = [
        input_root / (start + timedelta(hours=index)).strftime("%Y%m%d%H.LDASIN_DOMAIN1")
        for index in range(24)
    ]
    missing = [path for path in hours if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} forcing hours; first: {missing[0]}")
    stamp = day.strftime("%Y%m%d")
    day_root = output_root / stamp
    direct_daily = os.environ.get("HYDRO_OPS_DIRECT_DAILY") == "1"
    command = [
        os.environ.get("HYDRO_OPS_PYTHON", sys.executable),
        str(project / "bin/reconcile_prism_precipitation_day.py"),
        *(str(path) for path in hours),
        "--prism",
        str(
            project
            / f"data/forcing/oregon_state/prism/an/4km/daily/ppt/{day:%Y/%m}"
            / f"prism_ppt_us_25m_{stamp}.nc"
        ),
        "--weights",
        str(project / "data/static/remapping/nwm_conus_1km/nwm_to_prism_conservative.nc"),
        "--diagnostics",
        str(day_root / f"prism_precipitation_diagnostics.{stamp}.nc"),
        "--revision",
        os.environ.get("HYDRO_OPS_PRISM_REVISION", "early"),
        "--max-iterations",
        os.environ.get("HYDRO_OPS_PRISM_MAX_ITERATIONS", "20"),
        "--force",
    ]
    if direct_daily:
        scratch = Path(
            f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
        )
        command.extend(
            (
                "--daily-output",
                str(day_root / f"{stamp}.LDASIN_DOMAIN1"),
                "--day",
                day.isoformat(),
                "--work-directory",
                str(scratch),
            )
        )
        if corrected_temperature := os.environ.get(
            "HYDRO_OPS_TEMPERATURE_CORRECTED_DAY"
        ):
            command.extend(("--temperature-corrected-day", corrected_temperature))
    else:
        command.extend(("--output-directory", str(day_root / "hourly")))
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
