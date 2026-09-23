"""Journal and replace pending batches only, after a passed writer benchmark."""
import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from retry_post2020_forcing_campaign import snapshot

from hydro_ops.config import load_settings


def pending_indices(states):
    return sorted(i for i, state in states.items() if state == "PENDING")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", type=Path, help="Resume a journaled cancellation, not a submitted job")
    args = parser.parse_args()
    if not args.job.isdigit():
        parser.error("Numeric job ID required")
    settings = load_settings()
    proof = json.loads((args.benchmark / "acceptance.json").read_text())
    benchmark = json.loads((args.benchmark / "submission.json").read_text())
    if proof.get("status") != "passed" or len(proof.get("days", [])) != 7 or not benchmark.get("all_optimizations"):
        raise ValueError("All-three benchmark acceptance required")
    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines()]
    states = snapshot(args.job)
    selected = pending_indices(states)
    prior = json.loads(args.resume.read_text()) if args.resume else None
    if prior:
        if prior["original_job"] != args.job or prior.get("replacement_job") or prior.get("command"):
            raise ValueError("Journal mismatch or submission already attempted; inspect scheduler first")
        selected = prior["selected_indices"]
    if not selected or max(states) >= len(tasks):
        raise ValueError("No pending tasks or inventory mismatch")
    for index in selected:
        task = tasks[index]
        if task["stream"] not in {"retro", "nrt"} or Path(task["output_root"]).resolve() != (
                settings.project_root / "forcing/outputs/conus" / task["stream"] / "hourly").resolve():
            raise ValueError("Unexpected task output root")
    plan = {"original_job": args.job, "original_tasks": str(args.tasks.resolve()),
            "original_states": states, "selected_indices": selected,
            "benchmark": benchmark, "workers": 8, "cpus_per_task": 64,
            "scratch_mb_per_task": 240000, "dependency": f"afterany:{args.job}"}
    if prior:
        plan = prior
    print(json.dumps(plan, indent=2), flush=True)
    if not args.apply:
        return
    directory = args.resume.parent if prior else settings.work_root / f"post2020-writer-upgrade-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    if not prior:
        directory.mkdir(parents=True, exist_ok=False)
    receipt = directory / "acceptance.json"
    receipt.write_text(json.dumps(proof, indent=2) + "\n")
    for task in tasks:
        task.update(writer_acceptance=str(receipt), writer_all_optimizations=True,
                    writer_profile="validated_chunks_v1" if task["stream"] == "retro" else "reference")
    frozen = directory / "tasks.jsonl"
    frozen.write_text("".join(json.dumps(task) + "\n" for task in tasks))
    record = directory / "submission.json"

    def save():
        temporary = record.with_suffix(".part")
        temporary.write_text(json.dumps(plan, indent=2) + "\n")
        temporary.replace(record)

    save()
    if not prior:
        subprocess.run(["scancel", "--state=PENDING", args.job], check=True)
    current = snapshot(args.job)
    plan["states_after_cancel"] = current
    selected = [i for i in selected if current.get(i) == "CANCELLED"]
    plan["replacement_indices"] = selected
    save()
    if any(current.get(i) not in {"CANCELLED", "RUNNING", "COMPLETING", "COMPLETED"}
           for i in plan["selected_indices"]):
        raise RuntimeError(f"Unresolved cancellation; inspect {record}")
    if not selected:
        return
    command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}",
               "--nodes=1", "--ntasks=1", "--cpus-per-task=64", "--tmp=240000", "--time=48:00:00",
               f"--array={','.join(map(str, selected))}%8", f"--dependency=afterany:{args.job}",
               "--job-name=nwm-cnrfc-prism-static-v4-fast-writers-pending-handoff",
               f"--output={settings.log_root}/post2020-rebuild-%A_%a.out",
               (f"--export=ALL,HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
                f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_REBUILD_TASK_FILE={frozen},"
                "HYDRO_OPS_REBUILD_STATIC_ENVELOPE=1"),
               str(settings.project_root / "slurm/rebuild_post2020_forcing.py")]
    if settings.slurm_account:
        command.insert(1, f"--account={settings.slurm_account}")
    plan["command"] = command
    save()
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    plan["replacement_job"] = result.stdout.strip().split(";")[0]
    save()
    print(json.dumps({"job": plan["replacement_job"], "batches": len(selected), "record": str(record)}))


if __name__ == "__main__":
    main()
