#!/usr/bin/env python3
"""Submit an inclusive range of daily-batched forcing-production tasks."""

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
    parser.add_argument("--max-concurrent", type=int, default=16)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    if args.max_concurrent <= 0:
        parser.error("--max-concurrent must be positive")

    tasks = (args.end - args.start).days + 1
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    exports = [
        "ALL",
        f"HYDRO_OPS_START_DAY={args.start.isoformat()}",
        f"HYDRO_OPS_PYTHON={sys.executable}",
    ]
    if args.force:
        exports.append("HYDRO_OPS_FORCE=1")
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        f"--array=0-{tasks - 1}%{args.max_concurrent}",
        f"--export={','.join(exports)}",
        f"--output={settings.log_root}/forcing-day-%A_%a.out",
        "slurm/produce_forcing_day.py",
    ]
    if settings.slurm_account:
        command.insert(2, f"--account={settings.slurm_account}")
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
