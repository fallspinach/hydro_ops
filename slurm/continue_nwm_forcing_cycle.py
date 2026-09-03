#!/usr/bin/env python3
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=1
# SBATCH --time=01:00:00
"""Continue a coordinated forcing cycle after source and baseline jobs finish."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    manifest = Path(os.environ["HYDRO_OPS_CYCLE_MANIFEST"])
    state = json.loads(manifest.read_text())
    command = [
        python,
        str(project / "bin/submit_prism_calendar_batches.py"),
        "--stream",
        state["stream"],
        "--start",
        state["start"],
        "--end",
        state["end"],
        "--max-concurrent",
        str(state["prism_concurrency"]),
        "--cpus-per-task",
        "12",
        "--tmp-mb",
        "120000",
    ]
    completed = subprocess.run(command, cwd=project, check=False, capture_output=True, text=True)
    print(completed.stdout, end="", flush=True)
    print(completed.stderr, end="", file=sys.stderr, flush=True)
    state["prism_submission_output"] = completed.stdout
    state["continuation_job_id"] = os.environ.get("SLURM_JOB_ID")
    if completed.returncode:
        state["status"] = "prism_submission_failed"
        manifest.write_text(json.dumps(state, indent=2) + "\n")
        return completed.returncode
    match = re.search(r"Submitted batch job (\d+)", completed.stdout)
    state["prism_job_id"] = match.group(1) if match else None
    state["status"] = "prism_submitted" if match else "complete_no_prism_work"
    if state["stream"] == "retro" and match:
        cleanup = subprocess.run(
            [
                "sbatch",
                f"--partition={state['partition']}",
                f"--dependency=afterok:{match.group(1)}",
                f"--job-name=nwm-retro-cleanup-{state['start'].replace('-', '')}-{state['end'].replace('-', '')}",
                "--cpus-per-task=1",
                "--time=04:00:00",
                (
                    "--export=ALL,"
                    f"HYDRO_OPS_PROJECT_ROOT={project},"
                    f"HYDRO_OPS_PYTHON={python},HYDRO_OPS_CLEANUP_START={state['start']},"
                    f"HYDRO_OPS_CLEANUP_END={state['end']}"
                ),
                f"--output={project}/logs/nwm-retro-cleanup-%j.out",
                str(project / "slurm/cleanup_stable_baseline.py"),
            ],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        print(cleanup.stdout, end="", flush=True)
        cleanup_match = re.search(r"Submitted batch job (\d+)", cleanup.stdout)
        state["cleanup_job_id"] = cleanup_match.group(1) if cleanup_match else None
    manifest.write_text(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
