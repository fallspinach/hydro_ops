#!/usr/bin/env python3
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=1
# SBATCH --time=48:00:00
"""Stage quota-aware baseline shards, then submit the convergence controller."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

JOB_ID = re.compile(r"Submitted batch job (\d+)")


def write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def run(command: list[str], project: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=project, check=False, capture_output=True, text=True)
    print(completed.stdout, end="", flush=True)
    print(completed.stderr, end="", file=sys.stderr, flush=True)
    return completed


def submit_with_quota_retry(
    command: list[str], project: Path, delay_seconds: int = 60
) -> subprocess.CompletedProcess[str]:
    while True:
        completed = run(command, project)
        if completed.returncode == 0:
            return completed
        if "MaxSubmitJobsPerAccount" not in completed.stdout + completed.stderr:
            return completed
        print(f"submission quota full; retrying in {delay_seconds} seconds", flush=True)
        time.sleep(delay_seconds)


def baseline_command(python: str, state: dict[str, Any], index: int) -> list[str]:
    shard = state["baseline_shards"][index]
    return [
        python,
        "bin/submit_forcing_days.py",
        "--start",
        shard["start"],
        "--end",
        shard["end"],
        "--missing-only",
        "--max-concurrent",
        str(shard["workers"]),
        "--cpus-per-task",
        "12",
        "--tmp-mb",
        "120000",
        "--job-name",
        f"nwm-retro-{state['start'][:4]}-{state['end'][:4]}-baseline-shard-{index + 1}",
    ]


def convergence_command(
    project: Path, python: str, manifest: Path, state: dict[str, Any]
) -> list[str]:
    exports = (
        "ALL,"
        f"HYDRO_OPS_PROJECT_ROOT={project},HYDRO_OPS_PYTHON={python},"
        f"HYDRO_OPS_CYCLE_MANIFEST={manifest}"
    )
    command = [
        "sbatch",
        f"--partition={state['partition']}",
        f"--job-name=nwm-retro-{state['start'][:4]}-{state['end'][:4]}-converge",
        "--cpus-per-task=1",
        "--time=48:00:00",
        "--requeue",
        f"--export={exports}",
        f"--output={project}/logs/nwm-retro-{state['start'][:4]}-{state['end'][:4]}-converge-%j.out",
        "slurm/converge_nwm_forcing_cycle.py",
    ]
    job_ids = state["baseline_job_ids"]
    if job_ids:
        command.insert(2, f"--dependency=afterany:{':'.join(job_ids)}")
    if state.get("account"):
        command.insert(2, f"--account={state['account']}")
    return command


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    manifest = Path(os.environ["HYDRO_OPS_CYCLE_MANIFEST"])
    state = json.loads(manifest.read_text())
    for index in range(state.get("submitted_shards", 0), len(state["baseline_shards"])):
        completed = submit_with_quota_retry(baseline_command(python, state, index), project)
        if completed.returncode:
            state["status"] = "baseline_submission_failed"
            write_state(manifest, state)
            return completed.returncode
        match = JOB_ID.search(completed.stdout)
        if match:
            state["baseline_job_ids"].append(match.group(1))
        elif "eligible_days=0" not in completed.stdout:
            state["status"] = "baseline_submission_failed"
            write_state(manifest, state)
            return 1
        state["submitted_shards"] = index + 1
        write_state(manifest, state)
    completed = submit_with_quota_retry(
        convergence_command(project, python, manifest, state), project
    )
    if completed.returncode:
        state["status"] = "convergence_submission_failed"
        write_state(manifest, state)
        return completed.returncode
    match = JOB_ID.search(completed.stdout)
    state["status"] = "submitted"
    state["convergence_job_id"] = match.group(1) if match else None
    write_state(manifest, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
