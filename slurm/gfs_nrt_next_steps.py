"""Isolated GFS overlap comparison or full-day NRT publication test."""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scratch = Path("/scratch") / os.environ["SLURM_JOB_USER"] / f"job_{os.environ['SLURM_JOB_ID']}"
scratch.mkdir(parents=True, exist_ok=True)
if os.environ.get("GFS_NEXT_STEP", "compare") == "compare":
    command = [sys.executable, "bin/compare_gfs_nrt_gap.py", "--work", str(scratch)]
elif os.environ.get("GFS_NEXT_STEP") == "cycle":
    command = [sys.executable, "bin/check_gfs_cycle_fallback.py", "--work", str(scratch)]
else:
    day = date.fromisoformat(os.environ.get("GFS_TEST_DAY", "20260824"))
    command = [sys.executable, "bin/produce_gfs_nrt_day.py", "--input",
        f"forcing/outputs/conus/nrt/{day:%Y/%m/%Y%m%d}.LDASIN_DOMAIN1", "--output",
        f"forcing/work/gfs-nrt-exploration/full_day/{day:%Y%m%d}.LDASIN_DOMAIN1",
        "--work", str(scratch), "--historical-test"]
subprocess.run(command, cwd=root, check=True)
