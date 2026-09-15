#!/usr/bin/env python3
"""Queue seasonal benchmarks and success-gated remaining retrospective blocks."""
from __future__ import annotations

import argparse
import getpass
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def accept(directory):
    reports = [json.loads((directory / f"benchmark-{month}.accepted.json").read_text())
               for month in ("200301", "200307")]
    for report, start, end in zip(reports, ("2003-01-01", "2003-07-01"),
                                  ("2003-01-31", "2003-07-31"), strict=True):
        if (report["status"], report["start"], report["end"], report["days"]) != ("passed", start, end, 31):
            raise ValueError("Incomplete seasonal benchmark")
    gate = {"status": "passed", "created": datetime.now(UTC).isoformat(), "benchmarks": reports,
            "note": "Scientific/publication success gate; measured throughput reported, not a promised SLA"}
    temp = directory / "gate.partial"
    temp.write_text(json.dumps(gate, indent=2) + "\n")
    temp.replace(directory / "gate.json")


def blocks():
    return [(f"{year}-01-01", f"{year + 1}-12-31") for year in range(2003, 2019, 2)] + [
        ("2019-01-01", "2020-10-13")]


def retry_failed(directory):
    """Preserve failed states, retry benchmarks, and rewire only their existing gate."""
    records = json.loads((directory / "submission.json").read_text())
    retry_path = directory / "retry-submission.json"
    if retry_path.exists():
        records[:2] = json.loads(retry_path.read_text())
    active = subprocess.check_output(["squeue", "-h", "-u", getpass.getuser(), "-o", "%i"], text=True).split()
    for item in records[:2]:
        if item["job"] in active:
            raise ValueError(f"Original benchmark still active: {item['job']}")
    for month, item in zip(("200301", "200307"), records[:2], strict=True):
        state = directory / f"benchmark-{month}.json"
        value = json.loads(state.read_text())
        startup_failed = False
        if value["status"] == "submitted" and not value.get("baseline_job_ids"):
            child = value.get("convergence_job_id")
            if child:
                statuses = subprocess.check_output([
                    "sacct", "-n", "-X", "-j", child, "--format=State", "--parsable2"
                ], text=True).strip().splitlines()
                startup_failed = statuses in (["FAILED|"], ["FAILED"])
        if value["status"] != "blocked_after_maximum_attempts" and not startup_failed:
            raise ValueError(f"Expected a failed, exhausted benchmark: {state}")
        backup = state.with_suffix(f".failed-{item['job']}.json") if retry_path.exists() else state.with_suffix(".initial-failed.json")
        if backup.exists():
            raise ValueError("State already preserved; investigate interrupted retry")
    previous_retry = retry_path.exists()
    if previous_retry:
        retry_path.rename(directory / f"retry-submission-{records[0]['job']}.json")
    retries = []
    for month, item in zip(("200301", "200307"), records[:2], strict=True):
        state = directory / f"benchmark-{month}.json"
        state.rename(state.with_suffix(f".failed-{item['job']}.json") if previous_retry
                     else state.with_suffix(".initial-failed.json"))
        command = [arg + "-retry" if arg.startswith("--job-name=") else arg
                   for arg in item["command"]]
        job = subprocess.check_output(command, text=True).strip().split(";")[0]
        retries.append({"job": job, "month": month, "command": command})
        retry_path.write_text(json.dumps(retries, indent=2) + "\n")
    dependency = "afterok:" + ":".join(item["job"] for item in retries)
    subprocess.run(["scontrol", "update", f"JobId={records[2]['job']}",
                    f"Dependency={dependency}"], check=True)
    print(json.dumps({"benchmarks": retries, "gate": records[2]["job"],
                      "dependency": dependency}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--accept", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.retry_failed:
        retry_failed(directory)
        return
    if args.accept:
        accept(directory)
        return
    if (directory / "submission.json").exists():
        parser.error("Campaign already submitted")
    project = Path(__file__).resolve().parents[1]
    from hydro_ops.config import load_settings

    settings = load_settings()
    records = []
    if args.submit:
        directory.mkdir(parents=True, exist_ok=True)

    def submit(label, arguments, dependency=None):
        command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}",
                   "--nodes=1", "--ntasks=1", "--cpus-per-task=1", "--time=48:00:00",
                   f"--chdir={project}", f"--job-name=retro-{label}",
                   f"--output={directory}/%x-%j.out"]
        if settings.slurm_account:
            command += [f"--account={settings.slurm_account}"]
        if dependency:
            command += [f"--dependency=afterok:{dependency}"]
        command += ["--wrap", shlex.join([sys.executable, *map(str, arguments)])]
        job = (subprocess.check_output(command, cwd=project, text=True).strip().split(";")[0]
               if args.submit else f"JOB_{len(records)}")
        records.append({"job": job, "label": label, "command": command})
        if args.submit:
            (directory / "submission.json").write_text(json.dumps(records, indent=2) + "\n")
        print(json.dumps(records[-1]), flush=True)
        return job

    benchmarks = []
    for month in ("01", "07"):
        benchmarks.append(submit(f"benchmark-2003{month}-baseline-prism-mask", [
            project / "bin/run_retro_forcing_block.py", "--start", f"2003-{month}-01",
            "--end", f"2003-{month}-31", "--baseline-workers", "21", "--prism-workers", "16",
            "--state", directory / f"benchmark-2003{month}.json"]))
    dependency = submit("benchmark-2003-winter-summer-acceptance", [
        project / "bin/submit_remaining_retro_forcing.py", "--directory", directory, "--accept"],
        ":".join(benchmarks))
    for start, end in blocks():
        label = f"{start.replace('-', '')}-{end.replace('-', '')}"
        dependency = submit(f"{label}-baseline-prism-mask-controller", [
            project / "bin/run_retro_forcing_block.py", "--start", start, "--end", end,
            "--state", directory / f"block-{label}.json", "--gate", directory / "gate.json"], dependency)


if __name__ == "__main__":
    main()
