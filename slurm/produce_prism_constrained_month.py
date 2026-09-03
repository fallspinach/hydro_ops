#!/usr/bin/env python3
# SBATCH --job-name=prism-month
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=64
# SBATCH --time=08:00:00
"""SLURM entry point for a low-memory monthly PRISM forcing pass."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    required = ("HYDRO_OPS_COMPLETE_ROOT", "HYDRO_OPS_OUTPUT_ROOT")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    if task_file := os.environ.get("HYDRO_OPS_MONTH_TASK_FILE"):
        year, month = (
            Path(task_file).read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])].split()
        )
    else:
        year = os.environ["HYDRO_OPS_YEAR"]
        month = os.environ["HYDRO_OPS_MONTH"]
    scratch = f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
    command = [
        os.environ.get("HYDRO_OPS_PYTHON", sys.executable),
        "bin/produce_prism_constrained_month.py",
        "--year",
        year,
        "--month",
        month,
        "--complete-root",
        os.environ["HYDRO_OPS_COMPLETE_ROOT"],
        "--output-root",
        os.environ["HYDRO_OPS_OUTPUT_ROOT"],
        "--work-directory",
        scratch,
    ]
    if stream := os.environ.get("HYDRO_OPS_FORCING_STREAM"):
        command.extend(["--stream", stream])
    if os.environ.get("HYDRO_OPS_FORCE") == "1":
        command.append("--force")
    if os.environ.get("HYDRO_OPS_DIAGNOSTICS_ONLY") == "1":
        command.append("--diagnostics-only")
    if os.environ.get("HYDRO_OPS_ALLOW_SYNTHETIC_TIMING") == "1":
        command.append("--allow-synthetic-timing")
    if weights := os.environ.get("HYDRO_OPS_PRECIPITATION_WEIGHTS"):
        command.extend(["--precipitation-weights", weights])
    if maximum_ratio := os.environ.get("HYDRO_OPS_MAXIMUM_RATIO"):
        command.extend(["--maximum-ratio", maximum_ratio])
    if maximum_hourly := os.environ.get("HYDRO_OPS_MAXIMUM_CORRECTED_HOURLY_DEPTH"):
        command.extend(["--maximum-corrected-hourly-depth", maximum_hourly])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
