#!/usr/bin/env python3
"""Submit one boundary-safe, globally throttled multi-year retro workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

from hydro_ops.config import load_settings


def shard_ranges(start: date, end: date, maximum_days: int = 1001) -> list[tuple[date, date]]:
    ranges = []
    first = start
    while first <= end:
        last = min(end, first + timedelta(days=maximum_days - 1))
        ranges.append((first, last))
        first = last + timedelta(days=1)
    return ranges


def allocate_workers(shards: list[tuple[date, date]], total: int) -> list[int]:
    """Distribute workers by shard size while assigning at least one per shard."""
    if total < len(shards):
        raise ValueError("worker count must be at least the number of shards")
    sizes = [(end - start).days + 1 for start, end in shards]
    quotas = [total * size / sum(sizes) for size in sizes]
    workers = [max(1, int(quota)) for quota in quotas]
    while sum(workers) < total:
        index = max(
            range(len(shards)),
            key=lambda candidate: quotas[candidate] - workers[candidate],
        )
        workers[index] += 1
    while sum(workers) > total:
        index = max(
            (candidate for candidate in range(len(shards)) if workers[candidate] > 1),
            key=lambda candidate: workers[candidate] - quotas[candidate],
        )
        workers[index] -= 1
    return workers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", required=True, type=int)
    parser.add_argument("--end-year", required=True, type=int)
    parser.add_argument("--baseline-workers", type=int, default=42)
    parser.add_argument("--prism-workers", type=int, default=32)
    parser.add_argument(
        "--cleanup-mode",
        choices=("immediate", "deferred"),
        default="deferred",
        help="defer cleanup when an adjacent multi-year block may run concurrently",
    )
    parser.add_argument("--dependency")
    parser.add_argument(
        "--existing-shard-job-id",
        action="append",
        default=[],
        help="resume after an already-submitted leading shard (repeat in shard order)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end_year < args.start_year or min(args.baseline_workers, args.prism_workers) < 1:
        parser.error("invalid year range or worker count")
    settings = load_settings()
    target_start = date(args.start_year, 1, 1)
    target_end = date(args.end_year, 12, 31)
    baseline_start = target_start - timedelta(days=1)
    baseline_end = target_end + timedelta(days=1)
    shards = shard_ranges(baseline_start, baseline_end)
    try:
        workers = allocate_workers(shards, args.baseline_workers)
    except ValueError as error:
        parser.error(str(error))
    if len(args.existing_shard_job_id) > len(shards):
        parser.error("more existing shard jobs than planned shards")
    plan = {
        "created": datetime.now(UTC).isoformat(),
        "cycle": "multi-year-retro",
        "stream": "retro",
        "start": target_start.isoformat(),
        "end": target_end.isoformat(),
        "baseline_shards": [
            {"start": first.isoformat(), "end": last.isoformat(), "workers": count}
            for (first, last), count in zip(shards, workers, strict=True)
        ],
        "baseline_workers": args.baseline_workers,
        "prism_concurrency": args.prism_workers,
        "maximum_attempts": 4,
        "cleanup_mode": args.cleanup_mode,
        "partition": settings.slurm_partition,
        "account": settings.slurm_account,
        "initial_dependency": args.dependency,
        "baseline_job_ids": args.existing_shard_job_id,
        "submitted_shards": len(args.existing_shard_job_id),
        "status": "planned" if args.dry_run else "staging",
    }
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    manifest = settings.work_root / f"nwm-forcing-cycle-multi-year-retro-{stamp}.json"
    plan["manifest"] = str(manifest.resolve())
    manifest.write_text(json.dumps(plan, indent=2) + "\n")
    exports = (
        "ALL,"
        f"HYDRO_OPS_PROJECT_ROOT={settings.project_root},HYDRO_OPS_PYTHON={sys.executable},"
        f"HYDRO_OPS_CYCLE_MANIFEST={manifest.resolve()}"
    )
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--job-name=nwm-retro-{args.start_year}-{args.end_year}-stage-shards",
        "--cpus-per-task=1",
        "--time=48:00:00",
        "--requeue",
        f"--export={exports}",
        f"--output={settings.log_root}/nwm-retro-{args.start_year}-{args.end_year}-stage-shards-%j.out",
        "slurm/submit_nwm_forcing_multi_year.py",
    ]
    dependency = (
        f"afterany:{':'.join(args.existing_shard_job_id)}"
        if args.existing_shard_job_id
        else args.dependency
    )
    if dependency:
        command.insert(2, f"--dependency={dependency}")
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    completed = subprocess.run(
        command, cwd=settings.project_root, check=False, capture_output=True, text=True
    )
    print(completed.stdout, end="")
    print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode == 0:
        plan["status"] = "staged"
        plan["staging_job_id"] = completed.stdout.strip().split()[-1]
        manifest.write_text(json.dumps(plan, indent=2) + "\n")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
