"""Replace failed/pending campaign batches, preserving running and completed work."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.config import load_settings


def snapshot(job: str) -> dict[int, str]:
    states = {}
    accounting = subprocess.check_output(
        ["sacct", "-j", job, "-X", "-n", "-P", "--format=JobID,State"], text=True)
    for line in accounting.splitlines():
        jid, state, *_ = line.split("|")
        suffix = jid.removeprefix(job + "_")
        if jid.startswith(job + "_") and suffix.isdigit():
            states[int(suffix)] = state.split()[0]
    queue = subprocess.check_output(
        ["squeue", "--array", "-j", job, "-h", "-o", "%i|%T"], text=True)
    for line in queue.splitlines():
        jid, state = line.strip().split("|")
        states[int(jid.removeprefix(job + "_"))] = state
    return states


def selected_indices(states: dict[int, str]) -> list[int]:
    return sorted(i for i, state in states.items() if state in {"FAILED", "PENDING"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--canaries", required=True, help="Original failed indices, comma separated")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.job.isdigit() or args.workers < 1:
        parser.error("job must be numeric and workers positive")
    settings = load_settings()
    tasks = args.tasks.read_text().splitlines()
    states = snapshot(args.job)
    if not states or max(states) >= len(tasks):
        raise ValueError("Accounting does not match supplied task inventory")
    selected = selected_indices(states)
    canaries = sorted({int(i) for i in args.canaries.split(",")})
    if not canaries or any(states.get(i) != "FAILED" for i in canaries):
        raise ValueError("Canaries must be failed original batches, not active or completed work")
    plan = {"original_job": args.job, "original_tasks": str(args.tasks.resolve()),
            "original_states": states, "selected_indices": selected, "canaries": canaries,
            "workers": args.workers, "cpus_per_task": 64, "scratch_mb_per_task": 240000,
            "static_envelope": True, "running_tasks_preserved": [i for i, s in states.items() if s == "RUNNING"]}
    print(json.dumps({"original_job": args.job, "selected_batches": len(selected),
                      "canaries": canaries, "preserved_running": plan["running_tasks_preserved"],
                      "workers": args.workers, "static_envelope": True}), flush=True)
    if not args.apply:
        return
    campaign = settings.work_root / f"post2020-retry-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    campaign.mkdir(parents=True, exist_ok=False)
    frozen = campaign / "tasks.jsonl"
    frozen.write_text("\n".join(tasks) + "\n")
    record = campaign / "submission.json"

    def save():
        partial = record.with_suffix(".json.part")
        partial.write_text(json.dumps(plan, indent=2) + "\n")
        partial.replace(record)

    save()
    # The state filter cannot cancel a worker that started after our snapshot.
    subprocess.run(["scancel", "--state=PENDING", args.job], check=True)
    current = snapshot(args.job)
    selected = [i for i in selected if current.get(i) not in {"RUNNING", "COMPLETED", "COMPLETING"}]
    if any(current.get(i) == "PENDING" for i in selected):
        raise RuntimeError(f"Pending originals still exist; inspect {record} before retrying")
    plan["selected_indices_after_cancel"] = selected
    save()

    def submit(indices, label, concurrency, dependency=None):
        command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}",
                   "--nodes=1", "--ntasks=1", "--cpus-per-task=64", "--tmp=240000",
                   "--time=48:00:00", f"--array={','.join(map(str, indices))}%{concurrency}",
                   f"--job-name=nwm-cnrfc-prism-static-v4-{label}-20201014-20260831",
                   f"--output={settings.log_root}/post2020-rebuild-%A_%a.out",
                   (f"--export=ALL,HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
                    f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_REBUILD_TASK_FILE={frozen},"
                    "HYDRO_OPS_REBUILD_STATIC_ENVELOPE=1"),
                   str(settings.project_root / "slurm/rebuild_post2020_forcing.py")]
        if dependency:
            command.insert(1, f"--dependency=afterok:{dependency}")
        if settings.slurm_account:
            command.insert(1, f"--account={settings.slurm_account}")
        result = subprocess.run(command, cwd=settings.project_root, text=True,
                                capture_output=True, check=True)
        job = result.stdout.strip().split(";")[0]
        plan[label] = {"job": job, "indices": indices, "command": command}
        save()
        print(f"{label}_job={job} record={record}", flush=True)
        return job

    pilot = submit(canaries, "canary", min(2, args.workers))
    remainder = [i for i in selected if i not in canaries]
    if remainder:
        submit(remainder, "remainder", args.workers, pilot)


if __name__ == "__main__":
    main()
