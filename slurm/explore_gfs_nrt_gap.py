"""Winter and summer week experiments; isolated from operational forcing."""

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

project = Path(__file__).resolve().parents[1]
index = int(os.environ.get("SLURM_ARRAY_TASK_ID", "-1"))
scratch = Path("/scratch") / os.environ["SLURM_JOB_USER"] / f"job_{os.environ['SLURM_JOB_ID']}"
command = [sys.executable, str(project / "bin/explore_gfs_nrt_gap.py"), "--work", str(scratch)]
if index < 0:
    command.append("--prepare-conservative" if os.environ.get("GFS_PREPARE_CONSERVATIVE") == "1" else "--prepare")
else:
    day = date(2026, 1 if index % 2 == 0 else 7, 15) + timedelta(days=index // 2)
    command.extend(["--day", day.isoformat()])
    if os.environ.get("GFS_CONSERVATIVE") == "1":
        command.append("--conservative")
subprocess.run(command, cwd=project, check=True)
