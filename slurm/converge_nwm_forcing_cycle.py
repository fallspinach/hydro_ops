#!/usr/bin/env python3
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --cpus-per-task=1
# SBATCH --time=48:00:00
"""Converge forcing through bounded audit, repair, publication, and cleanup."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

configured_python = os.environ.get("HYDRO_OPS_PYTHON")
if configured_python and Path(sys.executable).resolve() != Path(configured_python).resolve():
    os.execv(configured_python, [configured_python, *sys.argv])

from netCDF4 import Dataset

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
    command: list[str], project: Path, *, delay_seconds: int = 60
) -> subprocess.CompletedProcess[str]:
    while True:
        completed = run(command, project)
        if completed.returncode == 0:
            return completed
        if "MaxSubmitJobsPerAccount" not in completed.stdout + completed.stderr:
            return completed
        print(f"submission quota full; retrying in {delay_seconds} seconds", flush=True)
        time.sleep(delay_seconds)


def wait_for_job(job_id: str, project: Path, *, poll_seconds: int = 60) -> None:
    while True:
        status = subprocess.run(
            ["squeue", "--noheader", "--jobs", job_id],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            return
        time.sleep(poll_seconds)


def forcing_path(root: Path, day: date) -> Path:
    return root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"


def accepted_output(path: Path, stream: str) -> bool:
    if os.environ.get("HYDRO_OPS_RETRO_NEW_PRODUCTION") == "1":
        from hydro_ops.forcing.retro_publication import complete
        if not complete(path):
            return False
    try:
        with Dataset(path) as data:
            if len(data.dimensions.get("time", ())) != 24:
                return False
            if str(getattr(data, "archive_granularity", "")) != "utc_calendar_day":
                return False
            if str(getattr(data, "prism_reconciliation_accepted", "false")).lower() != "true":
                return False
            if stream != "retro":
                return True
            if "prism_precipitation_revision" in data.ncattrs():
                return data.getncattr("prism_precipitation_revision") == "stable"
            if "prism_precipitation_revisions" in data.ncattrs():
                revisions = json.loads(data.getncattr("prism_precipitation_revisions"))
                return bool(revisions) and set(revisions.values()) == {"stable"}
            return False
    except (OSError, RuntimeError, KeyError, AttributeError, json.JSONDecodeError):
        return False


def unresolved_days(root: Path, start: date, end: date, stream: str) -> list[date]:
    result = []
    day = start
    while day <= end:
        if not accepted_output(forcing_path(root, day), stream):
            result.append(day)
        day += timedelta(days=1)
    return result


def submitted_job_id(completed: subprocess.CompletedProcess[str]) -> str | None:
    match = JOB_ID.search(completed.stdout)
    return match.group(1) if match else None


def baseline_command(
    python: str, project: Path, missing: list[date], state: dict[str, Any]
) -> list[str]:
    dependencies = sorted(
        {target + timedelta(days=offset) for target in missing for offset in (-1, 0, 1)}
    )
    return [
        python,
        str(project / "bin/submit_forcing_days.py"),
        "--start",
        dependencies[0].isoformat(),
        "--end",
        dependencies[-1].isoformat(),
        "--only-days",
        *(day.isoformat() for day in dependencies),
        "--missing-only",
        "--max-concurrent",
        str(state.get("baseline_workers", 16)),
        "--cpus-per-task",
        "12",
        "--tmp-mb",
        str(state.get("scratch_mb", 120000)),
        "--job-name",
        f"nwm-converge-baseline-{state['start'].replace('-', '')}-{state['end'].replace('-', '')}",
    ]


def prism_command(python: str, project: Path, state: dict[str, Any]) -> list[str]:
    return [
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
        str(state.get("scratch_mb", 120000)),
    ]


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    manifest = Path(os.environ["HYDRO_OPS_CYCLE_MANIFEST"])
    state = json.loads(manifest.read_text())
    start, end = date.fromisoformat(state["start"]), date.fromisoformat(state["end"])
    output_root = project / "forcing/outputs/conus" / state["stream"]
    maximum = int(state.get("maximum_attempts", 4))
    history = state.setdefault("convergence_attempts", [])
    state["continuation_job_id"] = os.environ.get("SLURM_JOB_ID")

    if state["stream"] == "nrt" and state.get("recent_nrt_gfs_active"):
        # Run the recent source-aware tail before legacy older-window convergence.
        # This serializes their boundary baseline access; retro jobs never enter here.
        command = ["sbatch", f"--partition={state['partition']}", "--nodes=1", "--ntasks=1",
                   "--cpus-per-task=64", "--tmp=240000", "--time=48:00:00",
                   f"--job-name=nwm-cycle-{state['cycle']}-recent-nrt-gfs",
                   f"--output={project}/forcing/logs/recent-nrt-gfs-%j.out",
                   (f"--export=ALL,HYDRO_OPS_PROJECT_ROOT={project},"
                    f"NRT_START={state['recent_nrt_start']},NRT_END={state['recent_nrt_end']},"
                    f"NRT_REQUESTED_AT={state['created']}"),
                   "--wrap", f"{python} {project}/slurm/run_recent_nrt.py"]
        if state.get("account"):
            command.insert(1, f"--account={state['account']}")
        submitted = submit_with_quota_retry(command, project)
        recent_id = submitted_job_id(submitted)
        state["recent_nrt_job_id"] = recent_id
        write_state(manifest, state)
        if submitted.returncode or not recent_id:
            raise RuntimeError("Recent NRT worker was not submitted")
        wait_for_job(recent_id, project)
        recent = json.loads((project / "forcing/status/nrt-gfs/latest.json").read_text())
        if (recent.get("status") != "passed" or recent.get("start") != state["recent_nrt_start"]
                or recent.get("end") != state["recent_nrt_end"] or recent.get("job_id") != recent_id):
            state["status"] = "blocked_recent_nrt"
            write_state(manifest, state)
            return 2
        end = date.fromisoformat(state["recent_nrt_start"]) - timedelta(days=1)
        # Downstream submission helpers use state['end']; retain the full cycle end separately.
        state["full_cycle_end"] = state["end"]
        state["end"] = end.isoformat()

    for attempt in range(1, maximum + 1):
        missing = unresolved_days(output_root, start, end, state["stream"])
        if not missing:
            break
        record: dict[str, Any] = {
            "stage": "baseline_repair",
            "attempt": attempt,
            "missing_target_days_before": len(missing),
        }
        state["status"] = "baseline_repair"
        state["unresolved_day_examples"] = [day.isoformat() for day in missing[:20]]
        history.append(record)
        write_state(manifest, state)
        completed = submit_with_quota_retry(
            baseline_command(python, project, missing, state), project
        )
        repair_id = submitted_job_id(completed)
        record.update(job_id=repair_id, submission_returncode=completed.returncode)
        write_state(manifest, state)
        if completed.returncode:
            state["status"] = "blocked_baseline_submission"
            write_state(manifest, state)
            return completed.returncode
        if repair_id:
            wait_for_job(repair_id, project)
        elif "eligible_days=0" in completed.stdout:
            break

    for attempt in range(1, maximum + 1):
        missing = unresolved_days(output_root, start, end, state["stream"])
        if not missing:
            break
        record = {
            "stage": "prism_publication",
            "attempt": attempt,
            "missing_target_days_before": len(missing),
        }
        state["status"] = "prism_repair"
        state["unresolved_day_examples"] = [day.isoformat() for day in missing[:20]]
        history.append(record)
        write_state(manifest, state)
        completed = submit_with_quota_retry(prism_command(python, project, state), project)
        prism_id = submitted_job_id(completed)
        record.update(job_id=prism_id, submission_returncode=completed.returncode)
        write_state(manifest, state)
        if completed.returncode:
            state["status"] = "blocked_prism_submission"
            write_state(manifest, state)
            return completed.returncode
        if prism_id:
            wait_for_job(prism_id, project)
        else:
            break

    missing = unresolved_days(output_root, start, end, state["stream"])
    state["unresolved_days"] = len(missing)
    state["unresolved_day_examples"] = [day.isoformat() for day in missing[:100]]
    if missing:
        state["status"] = "blocked_after_maximum_attempts"
        write_state(manifest, state)
        return 2

    if state["stream"] == "retro" and state.get("cleanup_mode", "immediate") == "immediate":
        state["status"] = "cleanup"
        write_state(manifest, state)
        cleanup = run(
            [
                python,
                str(project / "bin/cleanup_stable_baseline.py"),
                "--project-root",
                str(project),
                "--start",
                state["start"],
                "--end",
                state["end"],
            ],
            project,
        )
        state["cleanup_returncode"] = cleanup.returncode
        if cleanup.returncode:
            state["status"] = "blocked_cleanup"
            write_state(manifest, state)
            return cleanup.returncode
    state["status"] = (
        "complete_pending_cleanup"
        if state["stream"] == "retro" and state.get("cleanup_mode") == "deferred"
        else "complete"
    )
    write_state(manifest, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
