#!/usr/bin/env python3
"""Submit monthly Stage-IV six-hour constraint-conversion tasks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--max-concurrent", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end < args.start or args.max_concurrent <= 0:
        parser.error("invalid date range or concurrency")
    months = (args.end.year - args.start.year) * 12 + args.end.month - args.start.month + 1
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{months - 1}%{args.max_concurrent}",
        "--job-name=stage4-6h-constraint-backfill",
        "--cpus-per-task=4",
        "--tmp=20000",
        (
            "--export=ALL,"
            f"HYDRO_OPS_STAGE4_6H_START={args.start.isoformat()},"
            f"HYDRO_OPS_STAGE4_6H_END={args.end.isoformat()},"
            f"HYDRO_OPS_PYTHON={sys.executable}"
        ),
        f"--output={settings.log_root}/stage4-6h-backfill-%A_%a.out",
        "slurm/backfill_stage4_six_hour_month.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
