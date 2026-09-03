#!/usr/bin/env python3
# SBATCH --job-name=audit-prism-monthly-retro-1979-1980
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=12
# SBATCH --tmp=120000
# SBATCH --time=48:00:00
"""Audit one 1979-1980 monthly PRISM publication per array task."""

import os
import subprocess
from pathlib import Path

index = int(os.environ["SLURM_ARRAY_TASK_ID"])
year, month = (1979 + index // 12, index % 12 + 1)
project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
python = os.environ["HYDRO_OPS_PYTHON"]
raise SystemExit(
    subprocess.run(
        [
            python,
            str(project / "bin/audit_monthly_prism_month.py"),
            "--year",
            str(year),
            "--month",
            str(month),
            "--baseline-root",
            str(project / "outputs/forcing/nwm/baseline"),
            "--retro-root",
            str(project / "outputs/forcing/nwm/retro"),
            "--report",
            str(
                project / f"outputs/forcing/validation/reports/monthly-prism/{year}{month:02d}.json"
            ),
        ],
        check=False,
    ).returncode
)
