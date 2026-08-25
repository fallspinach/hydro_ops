#!/usr/bin/env python3
#SBATCH --job-name=stage4-grib1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=48:00:00
"""Convert one monthly nested Stage-IV GRIB1 archive selected by array index."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def main() -> int:
    year, month = add_months(2002, 1, int(os.environ["SLURM_ARRAY_TASK_ID"]))
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    archive = (
        project / "data/forcing/noaa/stage4/archive" / f"{year:04d}"
        / f"stage4.{year:04d}{month:02d}.tar"
    )
    template = (
        project / "data/forcing/noaa/stage4/netcdf/archive/2020/07"
        / "stage4_archive_01h.20200701.nc"
    )
    if not archive.is_file():
        print(f"Missing monthly archive: {archive}", file=sys.stderr)
        return 1
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    command = [
        python,
        str(project / "bin/convert_stage4_legacy_month.py"),
        str(archive),
        "--grid-template",
        str(template),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
