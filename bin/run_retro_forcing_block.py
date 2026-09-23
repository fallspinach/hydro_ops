#!/usr/bin/env python3
"""Wait for a bounded production block, then audit every final publication.

Unlike a staging job, this job succeeds only after its descendant jobs and final
files pass. It can therefore be used safely in an afterok block chain.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import runpy
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date


def write(path, value):
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def audit(project, start, end, mask_hash):
    accepted = runpy.run_path(str(project / "slurm/converge_nwm_forcing_cycle.py"))["accepted_output"]
    count = 0
    day = start
    while day <= end:
        path = project / "forcing/outputs/conus/retro/hourly" / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")
        if not accepted(path, "retro"):
            raise ValueError(f"Unaccepted stable publication: {path}")
        with Dataset(path) as ds:
            var = ds["time"]
            values = num2date(var[:], var.units)
            if [(t.year, t.month, t.day, t.hour, t.minute, t.second) for t in values] != [
                (day.year, day.month, day.day, h, 0, 0) for h in range(24)
            ]:
                raise ValueError(f"Incorrect calendar hours: {path}")
            if getattr(ds, "forcing_static_mask_sha256", "") != mask_hash:
                raise ValueError(f"Missing current static envelope: {path}")
            if day >= date(2020, 7, 1) and not getattr(ds, "cnrfc_stage4_policy", ""):
                raise ValueError(f"Missing CNRFC policy: {path}")
        manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text())
        envelope = manifest["static_envelope"]
        stat = path.stat()
        actual = {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        if envelope["published_identity"] != actual or envelope["mask_sha256"] != mask_hash:
            raise ValueError(f"Stale mask audit identity: {path}")
        report = json.loads(Path(envelope["audit"]).read_text())
        if report["status"] != "published":
            raise ValueError(f"Incomplete masking journal: {path}")
        count += 1
        day += timedelta(days=1)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--baseline-workers", type=int, default=42)
    parser.add_argument("--prism-workers", type=int, default=32)
    parser.add_argument("--gate", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if args.end < args.start:
        parser.error("Reversed range")
    if args.gate and json.loads(args.gate.read_text()).get("status") != "passed":
        raise RuntimeError("Benchmark acceptance gate has not passed")
    helpers = runpy.run_path(str(project / "bin/update_nwm_forcing_multi_year.py"))
    shards = helpers["shard_ranges"](args.start - timedelta(days=1), args.end + timedelta(days=1))
    workers = helpers["allocate_workers"](shards, args.baseline_workers)
    from hydro_ops.config import load_settings

    settings = load_settings()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    if args.state.exists():
        raise RuntimeError("State already exists; review before resubmission")
    state = {"created": datetime.now(UTC).isoformat(), "cycle": "multi-year-retro", "stream": "retro",
             "start": str(args.start), "end": str(args.end), "baseline_workers": args.baseline_workers,
             "prism_concurrency": args.prism_workers, "maximum_attempts": 4,
             "scratch_mb": 240000,
             "writer_profile": "validated_chunks_v1",
             "cleanup_mode": "deferred", "partition": settings.slurm_partition,
             "account": settings.slurm_account, "baseline_job_ids": [], "submitted_shards": 0,
             "status": "staging", "manifest": str(args.state.resolve()),
             "baseline_shards": [{"start": str(a), "end": str(b), "workers": n}
                                 for (a, b), n in zip(shards, workers, strict=True)]}
    original = args.state.with_suffix(".initial-failed.json")
    if original.exists():
        state["original_attempt_state"] = str(original)
        state["timing_scope"] = "retry with existing baselines; exclude from fresh end-to-end throughput"
    write(args.state, state)
    env = {**os.environ, "HYDRO_OPS_PROJECT_ROOT": str(project), "HYDRO_OPS_PYTHON": sys.executable,
           "HYDRO_OPS_CYCLE_MANIFEST": str(args.state.resolve()),
           "HYDRO_OPS_REBUILD_STATIC_ENVELOPE": "1",
           "HYDRO_OPS_RETRO_NEW_PRODUCTION": "1",
           "HYDRO_OPS_RETRO_WRITER_PROFILE": "validated_chunks_v1",
           "HYDRO_OPS_MIN_SCRATCH_FREE_GB": "120"}
    # Enable validated writers only at PRISM submission, not baseline generation.
    # Do not inherit unrelated low-level experimental flags from the shell.
    for key in ("HYDRO_OPS_ARCHIVE_CHUNKS", "HYDRO_OPS_BENCH_FAST_MASK"):
        env.pop(key, None)
    subprocess.run([sys.executable, "slurm/submit_nwm_forcing_multi_year.py"],
                   cwd=project, env=env, check=True)
    state = json.loads(args.state.read_text())
    job = state["convergence_job_id"]
    while job in subprocess.check_output(
        ["squeue", "-h", "-u", getpass.getuser(), "-o", "%i"], text=True
    ).split():
        time.sleep(60)
    state = json.loads(args.state.read_text())
    if state["status"] != "complete_pending_cleanup":
        raise RuntimeError(f"Block did not converge: {state['status']}")
    mask = project / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"
    with Dataset(mask) as grid:
        mask_hash = str(grid.keep_sha256)
    count = audit(project, args.start, args.end, mask_hash)
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(state["created"])).total_seconds()
    result = {"status": "passed", "start": str(args.start), "end": str(args.end), "days": count,
              "elapsed_seconds": elapsed, "calendar_days_per_wall_hour": count / (elapsed / 3600),
              "baseline_workers": args.baseline_workers, "prism_workers": args.prism_workers,
              "mask_sha256": mask_hash, "state": str(args.state), "job_id": os.environ.get("SLURM_JOB_ID"),
              "cleanup": "deferred; all baseline including boundaries retained"}
    if state.get("original_attempt_state"):
        result["original_attempt_state"] = state["original_attempt_state"]
        result["timing_scope"] = state["timing_scope"]
    write(args.state.with_suffix(".accepted.json"), result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
