#!/usr/bin/env python3
#SBATCH --job-name=forcing-day
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --time=04:00:00
"""SLURM array entry point: one UTC day per task."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

configured_python = os.environ.get("HYDRO_OPS_PYTHON")
if configured_python and Path(sys.executable).resolve() != Path(configured_python).resolve():
    os.execv(configured_python, [configured_python, *sys.argv])

from hydro_ops.forcing.daily_archive import verified_daily_archive


def main() -> int:
    index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    if task_file := os.environ.get("HYDRO_OPS_FORCING_DAY_TASK_FILE"):
        day = date.fromisoformat(Path(task_file).read_text().splitlines()[index])
    else:
        start = date.fromisoformat(os.environ["HYDRO_OPS_START_DAY"])
        day = start + timedelta(days=index)
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    output_root = os.environ.get("HYDRO_OPS_OUTPUT_ROOT")
    if output_root and os.environ.get("HYDRO_OPS_ARCHIVE_DAILY") == "1":
        daily = Path(output_root) / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        legacy_daily = daily.with_suffix(f"{daily.suffix}.nc")
        if os.environ.get("HYDRO_OPS_FORCE") != "1" and (
            verified_daily_archive(daily, day)
            or verified_daily_archive(legacy_daily, day)
        ):
            print(f"SKIP verified daily archive {daily}", flush=True)
            return 0
    scratch = (
        f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
        f"/forcing-day-{day:%Y%m%d}"
    )
    command = [
        python,
        "bin/produce_forcing_day.py",
        "--day",
        day.isoformat(),
        "--work-directory",
        scratch,
        "--project-root",
        os.environ.get("HYDRO_OPS_LAYOUT_ROOT", "."),
        "--assembly-workers",
        os.environ.get("HYDRO_OPS_ASSEMBLY_WORKERS", "4"),
        "--precipitation-remap-workers",
        os.environ.get("HYDRO_OPS_PRECIPITATION_REMAP_WORKERS", "1"),
    ]
    if output_root:
        command.extend(["--output-root", output_root])
    if os.environ.get("HYDRO_OPS_FORCE") == "1":
        command.append("--force")
    produced = subprocess.run(command, check=False)
    if produced.returncode != 0 or os.environ.get("HYDRO_OPS_ARCHIVE_DAILY") != "1":
        return produced.returncode
    assert output_root is not None
    archive = [
        python,
        "bin/archive_nwm_forcing_day.py",
        "--day",
        day.isoformat(),
        "--hourly-root",
        output_root,
        "--output-root",
        output_root,
        "--work-directory",
        scratch,
        "--delete-hourly",
    ]
    if os.environ.get("HYDRO_OPS_FORCE") == "1":
        archive.append("--force")
    return subprocess.run(archive, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
