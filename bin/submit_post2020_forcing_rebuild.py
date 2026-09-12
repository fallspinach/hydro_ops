#!/usr/bin/env python3
"""Submit a pilot-gated rebuild of existing post-2020 retro and NRT forcing."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-days", type=int, default=7)
    args = parser.parse_args()
    if min(args.workers, args.batch_days) < 1:
        parser.error("workers and batch-days must be positive")
    settings = load_settings()
    pilot_day = date(2026, 4, 12)
    tasks = []
    missing_prism = set()
    inventory = {}
    for stream in ("retro", "nrt"):
        root = settings.project_root / "forcing/outputs/conus" / stream
        days = sorted(
            date.fromisoformat(path.name[:8])
            for path in root.glob("*/*/*.LDASIN_DOMAIN1")
            if path.name[:8] >= "20201014"
        )
        inventory[stream] = {"days": len(days), "first": str(min(days)), "last": str(max(days))}
        for day in days:
            for constraint_day in (day, day + timedelta(days=1)):
                for variable in ("ppt", "tmin", "tmax"):
                    path = (settings.prism_data_dir / variable / constraint_day.strftime("%Y/%m")
                            / f"prism_{variable}_us_25m_{constraint_day:%Y%m%d}.nc")
                    if not path.is_file():
                        missing_prism.add(str(path))
            if stream == "nrt" and day == pilot_day:
                continue
            if (tasks and tasks[-1]["stream"] == stream
                    and day == date.fromisoformat(tasks[-1]["end"]) + timedelta(days=1)
                    and (day - date.fromisoformat(tasks[-1]["start"])).days < args.batch_days):
                tasks[-1]["end"] = str(day)
            else:
                tasks.append({"start": str(day), "end": str(day), "stream": stream,
                              "revision": "stable" if stream == "retro" else "provisional",
                              "output_root": str(root)})
    pilot = {"start": str(pilot_day), "end": str(pilot_day), "stream": "nrt",
             "revision": "provisional",
             "output_root": str(settings.project_root / "forcing/outputs/conus/nrt")}
    plan = {"inventory": inventory, "batches": len(tasks), "workers": args.workers,
            "cpus_per_worker": 64, "scratch_mb_per_worker": 240000,
            "pilot": pilot, "missing_prism": sorted(missing_prism)}
    print(json.dumps(plan, indent=2), flush=True)
    if missing_prism:
        raise SystemExit("PRISM prerequisites are missing; no jobs submitted")
    if args.dry_run:
        return 0
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    settings.work_root.mkdir(parents=True, exist_ok=True)
    settings.log_root.mkdir(parents=True, exist_ok=True)

    def submit(items, label, workers, dependency=None):
        task_file = settings.work_root / f"post2020-rebuild-{stamp}-{label}.jsonl"
        task_file.write_text("".join(json.dumps(item) + "\n" for item in items))
        command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}",
                   f"--array=0-{len(items)-1}%{workers}", "--nodes=1", "--ntasks=1",
                   "--cpus-per-task=64", "--tmp=240000", "--time=48:00:00",
                   f"--job-name=nwm-cnrfc-prism-domain-{label}-20201014-onward",
                   f"--output={settings.log_root}/post2020-rebuild-%A_%a.out",
                   (f"--export=ALL,HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
                    f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_REBUILD_TASK_FILE={task_file}"),
                   "slurm/rebuild_post2020_forcing.py"]
        if dependency:
            command.insert(1, f"--dependency=afterok:{dependency}")
        if settings.slurm_account:
            command.insert(1, f"--account={settings.slurm_account}")
        result = subprocess.run(command, cwd=settings.project_root, text=True,
                                capture_output=True, check=True)
        job = result.stdout.strip().split(";")[0]
        print(f"{label}_job={job}", flush=True)
        return job

    plan["pilot_job"] = submit([pilot], "pilot", 1)
    plan["campaign_job"] = submit(tasks, "campaign", args.workers, plan["pilot_job"])
    (settings.work_root / f"post2020-rebuild-{stamp}-plan.json").write_text(
        json.dumps(plan, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
