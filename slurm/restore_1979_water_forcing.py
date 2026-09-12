"""Reconstruct an affected 1979 month, restoring only retired-v2 inactive cells."""

from __future__ import annotations

import calendar
import os
import subprocess
from datetime import date
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ["HYDRO_OPS_PYTHON"]
    month = int(os.environ["SLURM_ARRAY_TASK_ID"]) + 1
    root = project / "forcing/work/restore-water-v3" / f"1979{month:02}"
    baseline, candidate = root / "baseline", root / "retro"
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    root.mkdir(parents=True, exist_ok=True)
    days = [date(1979, month, index) for index in range(1, calendar.monthrange(1979, month)[1] + 1)]
    tasks = root / "baseline-days.txt"
    tasks.write_text("".join(f"{day}\n" for day in days))
    for index, day in enumerate(days):
        env = {**os.environ, "SLURM_ARRAY_TASK_ID": str(index),
               "HYDRO_OPS_FORCING_DAY_TASK_FILE": str(tasks),
               "HYDRO_OPS_OUTPUT_ROOT": str(baseline), "HYDRO_OPS_LAYOUT_ROOT": str(project),
               "HYDRO_OPS_ARCHIVE_DAILY": "1", "HYDRO_OPS_FORCE": "1",
               "HYDRO_OPS_START_HOUR": "13" if day == date(1979, 1, 1) else ""}
        subprocess.run([python, "slurm/produce_forcing_day.py"], env=env, cwd=project, check=True)
    subprocess.run(
        [python, "bin/produce_prism_constrained_month.py", "--year", "1979", "--month", str(month),
         "--complete-root", str(baseline), "--output-root", str(candidate),
         "--work-directory", str(scratch), "--maximum-ratio", "100", "--allow-synthetic-timing",
         "--force"], cwd=project, check=True,
    )
    for day in days:
        relative = day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")
        subprocess.run(
            [python, "bin/restore_inactive_forcing_values.py",
             str(project / "forcing/outputs/conus/retro" / relative),
             str(candidate / relative), "--work-directory", str(scratch),
             "--backup-directory", str(root / "before-restoration")], cwd=project, check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
