#!/usr/bin/env python3
#SBATCH --job-name=nwm-forcing-domain-repair
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=04:00:00
"""Repair one daily LDASIN per array task using node-local scratch."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    task_file = Path(os.environ["HYDRO_OPS_DOMAIN_REPAIR_TASK_FILE"])
    source = Path(task_file.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    command = [
        python,
        str(project / "bin/repair_nwm_forcing_domain.py"),
        str(source),
        "--in-place",
        "--work-directory",
        str(scratch),
    ]
    if os.environ.get("HYDRO_OPS_DOMAIN_AUDIT_ONLY") == "1":
        command.append("--audit-only")
    return subprocess.run(
        command,
        cwd=project,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
