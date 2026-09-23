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

from hydro_ops.forcing.baseline_publication import accepted_baseline


def domain_repaired(path: Path, day: date) -> bool:
    if not path.is_file():
        return False
    from netCDF4 import Dataset

    with Dataset(path) as data:
        validated = (
            str(getattr(data, "forcing_domain_policy", ""))
            == "nldas2_active_gaps_preserve_inactive_v3"
            and str(getattr(data, "forcing_domain_content_audit", ""))
            == "all_records_active_complete_outside_masked_inactive_preserved_v3"
        )
        if day >= date(2020, 7, 1):
            validated = validated and bool(getattr(data, "cnrfc_stage4_policy", ""))
        return validated


def main() -> int:
    index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    if task_file := os.environ.get("HYDRO_OPS_FORCING_DAY_TASK_FILE"):
        day = date.fromisoformat(Path(task_file).read_text().splitlines()[index])
    else:
        start = date.fromisoformat(os.environ["HYDRO_OPS_START_DAY"])
        day = start + timedelta(days=index)
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    output_root = os.environ.get("HYDRO_OPS_OUTPUT_ROOT")
    force = os.environ.get("HYDRO_OPS_FORCE") == "1"
    if output_root and os.environ.get("HYDRO_OPS_ARCHIVE_DAILY") == "1":
        daily = Path(output_root) / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        legacy_daily = daily.with_suffix(f"{daily.suffix}.nc")
        if not force and (accepted_baseline(daily, day) or accepted_baseline(legacy_daily, day)):
            print(f"SKIP verified daily archive {daily}", flush=True)
            return 0
        # Existing but unaccepted archives require a real rebuild, including
        # hourly intermediates; never reuse old science just to add metadata.
        force = force or daily.exists() or legacy_daily.exists()
    scratch = (
        f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
        f"/forcing-day-{day:%Y%m%d}"
    )
    from hydro_ops.forcing.retro_publication import check_scratch
    check_scratch(scratch)
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
    start_hour = os.environ.get("HYDRO_OPS_START_HOUR")
    if cache := os.environ.get('HYDRO_OPS_PRECIPITATION_CACHE'):
        command.extend(['--precipitation-cache', cache])
    if start_hour:
        command.extend(["--start-hour", start_hour])
    if output_root:
        command.extend(["--output-root", output_root])
    if force:
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
    if force:
        archive.append("--force")
    if start_hour:
        archive.extend(["--start-hour", start_hour])
    archived = subprocess.run(archive, check=False)
    if archived.returncode:
        return archived.returncode
    daily = Path(output_root) / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
    return subprocess.run(
        [
            python,
            "bin/repair_nwm_forcing_domain.py",
            str(daily),
            "--in-place",
            "--work-directory",
            scratch,
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
