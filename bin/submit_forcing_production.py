#!/usr/bin/env python3
"""Submit a bounded, resumable rolling forcing-production SLURM array."""

from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument("--lag-hours", type=int, default=3)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.lookback_hours <= 0 or args.lag_hours < 0 or args.max_concurrent <= 0:
        parser.error("lookback/concurrency must be positive and lag must be nonnegative")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    end = now - timedelta(hours=args.lag_hours)
    start = end - timedelta(hours=args.lookback_hours - 1)
    tasks = args.lookback_hours
    if not args.force:
        result = subprocess.run(
            ["squeue", "--noheader", "--user", getpass.getuser(), "--name", "forcing-production"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip():
            print("SKIP active forcing-production job")
            return 0
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{tasks - 1}%{args.max_concurrent}",
        f"--export=ALL,HYDRO_OPS_START={start:%Y%m%d%H},HYDRO_OPS_PYTHON={sys.executable}",
        f"--output={settings.log_root}/forcing-production-%A_%a.out",
        "slurm/produce_forcing.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
