#!/usr/bin/env python3
"""Submit full scans of selected daily forcing outputs as a bounded SLURM array."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--stream", required=True, choices=("nrt", "retro"))
    parser.add_argument("--revision", choices=("early", "provisional", "stable"))
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--report-root", required=True, type=Path)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end < args.start or args.max_concurrent < 1:
        parser.error("date range and concurrency must be positive")
    tasks = []
    day = args.start
    while day <= args.end:
        path = args.root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        if not path.is_file():
            path = path.with_suffix(f"{path.suffix}.nc")
        if path.is_file():
            tasks.append(
                {
                    "path": str(path.resolve()),
                    "report": str(
                        (
                            args.report_root
                            / f"{args.scenario}.{args.stream}.{day:%Y%m%d}.json"
                        ).resolve()
                    ),
                    "scenario": args.scenario,
                    "stream": args.stream,
                    "revision": args.revision,
                }
            )
        day += timedelta(days=1)
    print(json.dumps({"eligible_validations": len(tasks), "tasks": tasks}, indent=2))
    if not tasks:
        return 0
    settings = load_settings()
    settings.work_root.mkdir(parents=True, exist_ok=True)
    settings.log_root.mkdir(parents=True, exist_ok=True)
    args.report_root.mkdir(parents=True, exist_ok=True)
    safe_scenario = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in args.scenario
    )
    task_file = settings.work_root / (
        f"forcing-validation-{safe_scenario}-{args.stream}-"
        f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}.jsonl"
    )
    if not args.dry_run:
        task_file.write_text("".join(json.dumps(task, sort_keys=True) + "\n" for task in tasks))
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{len(tasks) - 1}%{args.max_concurrent}",
        (
            "--export=ALL,"
            f"HYDRO_OPS_PYTHON={sys.executable},"
            f"HYDRO_OPS_VALIDATION_TASK_FILE={task_file.resolve()}"
        ),
        f"--output={settings.log_root}/forcing-validation-%A_%a.out",
        "slurm/validate_nwm_forcing_day.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
