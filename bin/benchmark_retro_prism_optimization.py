#!/usr/bin/env python3
"""Paired, isolated PRISM/calendar/mask benchmark using existing 2003 baselines."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.retro_publication import complete

PROJECT = Path(__file__).resolve().parents[1]
STAGES = {
    "produce_prism_constrained_daily.py": "prism_window",
    "materialize_calendar_forcing.py": "calendar_assembly",
    "repair_nwm_forcing_domain.py": "active_cell_repair",
    "apply_static_forcing_mask.py": "static_mask_and_publication",
}


def write(path, value):
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def child_environment(mode, script, window_writing=False):
    env = dict(os.environ)
    # Only calendar assembly uses raw chunks. PRISM override writing and legacy
    # provenance normalization remain identical in both arms.
    chunks = ((mode == "optimized" or window_writing) and script == "materialize_calendar_forcing.py")
    chunks |= window_writing and mode == "optimized" and script == "produce_prism_constrained_daily.py"
    env["HYDRO_OPS_ARCHIVE_CHUNKS"] = "1" if chunks else "0"
    return env


def run_trial(directory, month, mode, window_writing=False):
    directory = directory.resolve()
    directory.relative_to(PROJECT / "forcing/work")
    trial = directory / f"2003{month:02d}" / mode
    trial.mkdir(parents=True, exist_ok=True)
    summary = trial / "timing.json"
    if summary.exists():
        raise ValueError("Trial already started; use a new benchmark directory")
    start, end = date(2003, month, 15), date(2003, month, 16)
    task = trial / "task.jsonl"
    task.write_text(json.dumps({"start": str(start), "end": str(end), "stream": "retro",
                               "revision": "stable", "baseline_root": str(PROJECT / "forcing/outputs/conus/baseline/hourly"),
                               "output_root": str(trial / "retro")}) + "\n")
    os.environ.update(HYDRO_OPS_PROJECT_ROOT=str(PROJECT), HYDRO_OPS_PYTHON=sys.executable,
                      HYDRO_OPS_PRISM_CALENDAR_TASK_FILE=str(task), SLURM_ARRAY_TASK_ID="0",
                      HYDRO_OPS_RETRO_NEW_PRODUCTION="1", HYDRO_OPS_REBUILD_STATIC_ENVELOPE="1",
                      HYDRO_OPS_MIN_SCRATCH_FREE_GB="120", HYDRO_OPS_ARCHIVE_CHUNKS="0",
                      HYDRO_OPS_BENCH_FAST_MASK="1" if mode == "optimized" or window_writing else "0",
                      OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    record = {"status": "running", "mode": mode, "start": str(start), "end": str(end),
              "window_writing": window_writing,
              "job": os.environ["SLURM_JOB_ID"], "cpus": 12, "scratch_mb": 240000,
              "created": datetime.now(UTC).isoformat(), "stages": [],
              "scope": "Existing baselines; includes initial support PRISM window, final transfer and publication checks"}
    write(summary, record)

    def timed_run(command, project):
        script = Path(command[1]).name
        event = {"stage": STAGES[script], "command": command,
                 "started": datetime.now(UTC).isoformat()}
        before = time.perf_counter()
        result = subprocess.run(command, cwd=project, env=child_environment(mode, script, window_writing), check=False)
        event.update(seconds=time.perf_counter() - before, returncode=result.returncode)
        if not result.returncode and script in ("materialize_calendar_forcing.py", "produce_prism_constrained_daily.py"):
            key = "--start" if script == "materialize_calendar_forcing.py" else "--day"
            day = date.fromisoformat(command[command.index(key) + 1])
            root = Path(command[command.index("--output-root") + 1])
            path = root / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1.manifest.json")
            manifest = json.loads(path.read_text())
            event["archive_writer"] = manifest.get("archive_writer", "reference")
            event["archive_timing"] = manifest.get("timing")
        record["stages"].append(event)
        write(summary, record)
        print(json.dumps(event), flush=True)
        if result.returncode:
            raise RuntimeError(f"Benchmark stage failed: {script}: {result.returncode}")

    spec = importlib.util.spec_from_file_location("calendar_trial", PROJECT / "slurm/produce_prism_calendar_batch.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    worker.run = timed_run
    before = time.perf_counter()
    try:
        worker.main()
        for day in (start, end):
            if not complete(trial / "retro" / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")):
                raise ValueError(f"Incomplete output: {mode} {day}")
        record["status"] = "passed"
    except BaseException as error:
        record.update(status="failed", error=str(error))
        raise
    finally:
        record["elapsed_seconds"] = time.perf_counter() - before
        record["stage_totals_seconds"] = {
            stage: sum(e["seconds"] for e in record["stages"] if e["stage"] == stage)
            for stage in STAGES.values()
        }
        write(summary, record)


def compare_files(left, right):
    """Full decoded bitwise comparison, including auxiliary fields and metadata."""
    with Dataset(left) as a, Dataset(right) as b:
        a.set_auto_maskandscale(False)
        b.set_auto_maskandscale(False)
        if {k: len(v) for k, v in a.dimensions.items()} != {k: len(v) for k, v in b.dimensions.items()}:
            raise ValueError("Dimensions differ")
        if set(a.variables) != set(b.variables):
            raise ValueError("Variable sets differ")
        for attr in ("archive_granularity", "prism_reconciliation_accepted", "prism_precipitation_revisions",
                     "forcing_domain_policy", "forcing_domain_content_audit", "forcing_static_mask_sha256"):
            if a.getncattr(attr) != b.getncattr(attr):
                raise ValueError(f"Policy differs: {attr}")
        for name, var in a.variables.items():
            other = b[name]
            if var.shape != other.shape or var.dtype != other.dtype or var.dimensions != other.dimensions:
                raise ValueError(f"Schema differs: {name}")
            if set(var.ncattrs()) != set(other.ncattrs()):
                raise ValueError(f"Attributes differ: {name}")
            for attr in var.ncattrs():
                np.testing.assert_array_equal(var.getncattr(attr), other.getncattr(attr))
            steps = range(var.shape[0]) if var.dimensions and var.dimensions[0] == "time" else [Ellipsis]
            for step in steps:
                if np.asarray(var[step]).tobytes() != np.asarray(other[step]).tobytes():
                    raise ValueError(f"Values differ: {name}/{step}")


def compare(directory):
    reports = []
    for month in (1, 7):
        root = directory / f"2003{month:02d}"
        timings = {mode: json.loads((root / mode / "timing.json").read_text())
                   for mode in ("reference", "optimized")}
        if any(t["status"] != "passed" for t in timings.values()):
            raise ValueError("Trial did not finish")
        if timings["reference"].get("window_writing") != timings["optimized"].get("window_writing"):
            raise ValueError("Inconsistent comparison configuration")
        for day in (15, 16):
            relative = Path(f"retro/2003/{month:02d}/2003{month:02d}{day:02d}.LDASIN_DOMAIN1")
            for mode in timings:
                if not complete(root / mode / relative):
                    raise ValueError("Missing/stale publication audit")
            compare_files(root / "reference" / relative, root / "optimized" / relative)
        events = timings["optimized"]["stages"]
        if any(e.get("archive_writer") != "compressed_chunks" for e in events if e["stage"] == "calendar_assembly"):
            raise ValueError("Calendar fast path fell back; do not claim a chunk benchmark")
        if timings["optimized"].get("window_writing") and any(
            e.get("archive_writer") != "compressed_chunks" for e in events if e["stage"] == "prism_window"
        ):
            raise ValueError("PRISM-window fast path fell back")
        for day in (15, 16):
            path = root / "optimized/retro" / f"2003/{month:02d}/2003{month:02d}{day:02d}.LDASIN_DOMAIN1.manifest.json"
            manifest = json.loads(path.read_text())
            audit = json.loads(Path(manifest["static_envelope"]["audit"]).read_text())
            if audit.get("validation_mode") != "embedded_source_audit_plus_chunk_integrity":
                raise ValueError("Fast mask used a full-read fallback; review timing")
        reports.append({"month": month, "days": 2, "timings": timings,
                        "speedup": timings["reference"]["elapsed_seconds"] / timings["optimized"]["elapsed_seconds"]})
    write(directory / "acceptance.json", {"status": "passed", "months": reports,
                                          "comparison": "all variables, all records, bitwise decoded equality",
                                          "production_switch": False})
    print(json.dumps({"status": "passed", "speedups": [r["speedup"] for r in reports]}), flush=True)


def submit(window_writing=False):
    from hydro_ops.config import load_settings

    settings = load_settings()
    prefix = "retro-prism-window-optimization-" if window_writing else "retro-prism-optimization-"
    directory = PROJECT / "forcing/work" / (prefix + datetime.now(UTC).strftime("%Y%m%dT%H%M%S"))
    directory.mkdir()
    jobs = []

    def queue(label, arguments, dependency=None, scratch=True):
        if window_writing:
            label = "window-" + label
            arguments = [*arguments, "--window-writing"]
        command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}", "--nodes=1",
                   "--ntasks=1", "--cpus-per-task=12", "--time=06:00:00", f"--chdir={PROJECT}",
                   f"--job-name=retro-prism-opt-{label}", f"--output={directory}/%x-%j.out"]
        if scratch:
            command.append("--tmp=240000")
        if settings.slurm_account:
            command.append(f"--account={settings.slurm_account}")
        if dependency:
            command.append("--dependency=afterok:" + dependency)
        command.extend(["--wrap", shlex.join([sys.executable, str(Path(__file__).resolve()), *arguments])])
        env = dict(os.environ)
        for key in ("HYDRO_OPS_PROFILE_DIRECTORY", "HYDRO_OPS_ARCHIVE_CHUNKS", "HYDRO_OPS_BENCH_FAST_MASK"):
            env.pop(key, None)
        job = subprocess.check_output(command, env=env, text=True).strip().split(";")[0]
        jobs.append({"label": label, "job": job, "command": command})
        write(directory / "submission.json", {"directory": str(directory), "jobs": jobs})
        return job

    build = []
    for month in (1, 7):
        for mode in ("reference", "optimized"):
            for offset in range(-1, 3):
                day = date(2003, month, 15) + timedelta(days=offset)
                path = PROJECT / "forcing/outputs/conus/baseline/hourly" / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")
                if not path.is_file():
                    raise FileNotFoundError(path)
            build.append(queue(f"2003{month:02d}15-16-{mode}", ["--directory", str(directory), "--run", mode, "--month", str(month)]))
    queue("200301-200307-exact-audit", ["--directory", str(directory), "--compare"], ":".join(build), scratch=False)
    print((directory / "submission.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--submit", action="store_true")
    action.add_argument("--run", choices=("reference", "optimized"))
    action.add_argument("--compare", action="store_true")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--month", type=int, choices=(1, 7))
    parser.add_argument("--window-writing", action="store_true",
                        help="Both arms use fast calendar/masking; optimize only PRISM-window writing")
    args = parser.parse_args()
    if args.submit:
        submit(args.window_writing)
    elif not args.directory or (args.run and not args.month):
        parser.error("--directory and (for --run) --month are required")
    elif args.compare:
        compare(args.directory)
    else:
        run_trial(args.directory, args.month, args.run, args.window_writing)


if __name__ == "__main__":
    main()
