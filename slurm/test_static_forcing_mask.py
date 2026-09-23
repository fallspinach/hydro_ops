"""Four independent, copy-only pilots spanning forcing-source eras."""

import os
import subprocess
import sys
from pathlib import Path

project = Path(__file__).resolve().parents[1]
day = ("19790515", "19850715", "20020715", "20210315")[int(os.environ["SLURM_ARRAY_TASK_ID"])]
root = project / "forcing/work/static-mask-pilot-20260912"
scratch = Path("/scratch") / os.environ["SLURM_JOB_USER"] / f"job_{os.environ['SLURM_JOB_ID']}"
scratch.mkdir(parents=True, exist_ok=True)
subprocess.run([
    sys.executable, str(project / "bin/test_static_forcing_mask.py"),
    "--mask", str(root / "nldas2-seven-met-union-v1.nc"),
    "--source", str(project / "forcing/outputs/conus/retro/hourly" / day[:4] / day[4:6] / f"{day}.LDASIN_DOMAIN1"),
    "--output", str(root / "retro" / day[:4] / day[4:6] / f"{day}.LDASIN_DOMAIN1"),
    "--work", str(scratch),
], check=True)
