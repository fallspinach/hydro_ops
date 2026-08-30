#!/usr/bin/env python3
#SBATCH --job-name=submit-prism-nrt-window
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
"""Submit a bounded NRT window and attach its child array to the layout gate."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    migration_job = os.environ["HYDRO_OPS_MIGRATION_JOB"]
    repair_jobs = os.environ["HYDRO_OPS_REPAIR_JOBS"].split(":")
    command = [
        python,
        str(project / "bin/submit_prism_forcing_updates.py"),
        "--stream",
        "nrt",
        "--start",
        os.environ["HYDRO_OPS_NRT_START"],
        "--end",
        os.environ["HYDRO_OPS_NRT_END"],
        "--max-concurrent",
        os.environ.get("HYDRO_OPS_NRT_MAX_CONCURRENT", "4"),
        "--cpus-per-task",
        "64",
        "--force",
    ]
    completed = subprocess.run(
        command, cwd=project, check=False, capture_output=True, text=True
    )
    print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    if completed.returncode:
        return completed.returncode
    dependencies = [*repair_jobs, os.environ["SLURM_JOB_ID"]]
    if match := re.search(r"Submitted batch job (\d+)", completed.stdout):
        dependencies.append(match.group(1))
    update = subprocess.run(
        [
            "scontrol",
            "update",
            f"JobId={migration_job}",
            f"Dependency=afterok:{':'.join(dependencies)}",
        ],
        cwd=project,
        check=False,
    )
    return update.returncode


if __name__ == "__main__":
    raise SystemExit(main())
