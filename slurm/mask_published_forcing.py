"""Apply the inactive-cell filter to a batch of existing daily forcing files."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    tasks = Path(os.environ["HYDRO_OPS_INACTIVE_MASK_TASKS"])
    paths = json.loads(tasks.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    scratch = f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
    for path in paths:
        subprocess.run(
            [os.environ["HYDRO_OPS_PYTHON"], "bin/repair_nwm_forcing_domain.py", path,
             "--in-place", "--active-gaps-only", "--preserve-active", "--work-directory", scratch],
            cwd=project, check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
