#!/usr/bin/env python3
"""Report forcing coverage and submit any refresh jobs not already active."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hydro_ops.config import load_settings
from hydro_ops.forcing.completeness import report_range
from hydro_ops.forcing_status import forcing_coverage, format_coverage


@dataclass(frozen=True)
class Workflow:
    source: str
    job_name: str


def _recent_repair_start(source: str, settings, end: date, lookback_days: int) -> date | None:
    """Return the earliest incomplete recent day for cheaply inventoried products."""
    start = end - timedelta(days=lookback_days - 1)
    if source == "nldas2":
        products = (("nldas2", settings.nldas_data_dir),)
    elif source == "hrrr":
        products = (("hrrr", settings.hrrr_data_dir),)
    elif source == "prism":
        products = tuple(
            (f"prism_{variable}", settings.prism_data_dir)
            for variable in settings.prism_variables
        )
    else:
        return None
    incomplete = [
        report.day
        for product, root in products
        for report in report_range(product, root, start, end)
        if not report.complete
    ]
    return min(incomplete, default=None)


def refresh_dates(source: str, settings, today: date, lookback_days: int) -> tuple[date, date] | None:
    """Plan one range that repairs recent holes and reaches the newest eligible day."""
    if source == "nldas2":
        end = today - timedelta(days=settings.nldas_lag_days)
        normal_start = end
    elif source == "hrrr":
        end = today - timedelta(days=settings.hrrr_lag_days)
        normal_start = end
    elif source == "prism":
        end = today - timedelta(days=settings.prism_lag_days)
        normal_start = end - timedelta(days=settings.prism_refresh_days)
    else:
        return None
    repair_start = _recent_repair_start(source, settings, end, lookback_days)
    return min(normal_start, repair_start) if repair_start else normal_start, end


WORKFLOWS = (
    Workflow("nldas2", "nldas2_download"),
    Workflow("stage4", "stage4_download"),
    Workflow("prism", "prism_download"),
    Workflow("hrrr", "hrrr_download"),
    Workflow("mrms", "mrms_download"),
)


def active_jobs(user: str) -> set[str]:
    result = subprocess.run(
        ["squeue", "--noheader", "--user", user, "--format=%j"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-only", action="store_true", help="report without submitting")
    parser.add_argument("--dry-run", action="store_true", help="print submissions without sbatch")
    parser.add_argument("--force", action="store_true", help="submit even if the job name is active")
    parser.add_argument(
        "--repair-lookback-days",
        type=int,
        default=14,
        help="inventory this many recent eligible days and repair holes (default: 14)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repair_lookback_days < 1:
        raise SystemExit("--repair-lookback-days must be positive")
    settings = load_settings()
    settings.work_root.mkdir(parents=True, exist_ok=True)
    lock_path = settings.work_root / "update_forcing.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"Another forcing update is active ({lock_path})", file=sys.stderr)
            return 1

        print(format_coverage(forcing_coverage(settings)), flush=True)
        if args.status_only:
            return 0

        try:
            active = active_jobs(getpass.getuser())
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"Could not query SLURM: {error}", file=sys.stderr)
            return 1

        failures = 0
        print("\nRefresh submissions:", flush=True)
        for workflow in WORKFLOWS:
            if not args.force and workflow.job_name in active:
                print(f"SKIP   {workflow.source:<8} active job {workflow.job_name}")
                continue
            command = [sys.executable, "-m", "hydro_ops.cli", "submit", workflow.source]
            planned = refresh_dates(
                workflow.source,
                settings,
                datetime.now(UTC).date(),
                args.repair_lookback_days,
            )
            if planned is not None:
                start, end = planned
                command.extend(("--start", start.isoformat(), "--end", end.isoformat()))
            if args.dry_run:
                print(f"DRYRUN {workflow.source:<8} {' '.join(command)}")
                continue
            result = subprocess.run(command, check=False)
            if result.returncode:
                failures += 1
                print(f"FAILED {workflow.source:<8} exit {result.returncode}", file=sys.stderr)
            else:
                print(f"SUBMIT {workflow.source}")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
