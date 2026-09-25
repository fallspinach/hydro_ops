#!/usr/bin/env python3
"""Submit one coordinated NWM forcing cycle with persistent run provenance."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

from hydro_ops.config import load_settings
from hydro_ops.forcing.nrt_cycle import activation, configuration, require_operational_gfs
from hydro_ops.forcing.streams import forcing_stream_root


def cycle_window(cycle: str, today: date) -> tuple[str, date, date, int, int]:
    if cycle == "six-hourly":
        end = today - timedelta(days=2)
        return "nrt", end - timedelta(days=9), end, 14, 8
    if cycle == "daily":
        end = today - timedelta(days=2)
        return "nrt", end - timedelta(days=199), end, 200, 12
    end = today - timedelta(days=183)
    return "retro", end - timedelta(days=44), end, 45, 8


def main() -> int:
    launched_at = datetime.now(UTC).isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", required=True, choices=("six-hourly", "daily", "monthly-retro"))
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument(
        "--dependency",
        help="optional SLURM dependency for the initial explicit-window baseline array",
    )
    parser.add_argument(
        "--skip-source-refresh",
        action="store_true",
        help="use the existing source archive (for explicit historical retro windows)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--revisions-only", action="store_true",
                        help="Internal daily continuation after latest-hour publication")
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be supplied together")
    if args.start and (args.cycle != "monthly-retro" or args.end < args.start):
        parser.error("explicit windows require monthly-retro and an ordered date range")
    if args.skip_source_refresh and not args.start:
        parser.error("--skip-source-refresh requires an explicit historical window")
    if args.dependency and not args.start:
        parser.error("--dependency requires an explicit historical window")
    settings = load_settings()
    stream, start, end, source_lookback, prism_concurrency = cycle_window(
        args.cycle, datetime.now(UTC).date()
    )
    explicit_window = args.start is not None
    if explicit_window:
        start, end = args.start, args.end
    settings.work_root.mkdir(parents=True, exist_ok=True)
    lock_path = settings.work_root / "update_nwm_forcing.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another coordinator submission is active", file=sys.stderr)
            return 1
        active = subprocess.run(
            ["squeue", "--noheader", "--user", getpass.getuser(), "--format=%A|%j"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        coordinator_prefix = f"nwm-cycle-{args.cycle}"
        stream_markers = (
            ("nwm-cycle-six-hourly", "nwm-cycle-daily", "prism-nrt-calendar-batches")
            if stream == "nrt"
            else ("nwm-cycle-monthly-retro", "prism-retro-calendar-batches")
        )
        if not explicit_window and any(
            any(marker in line for marker in stream_markers) for line in active
        ):
            print(f"SKIP active coordinated {stream} workflow ({coordinator_prefix})")
            return 0
        plan = {
            "created": launched_at,
            "cycle": args.cycle,
            "stream": stream,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "source_lookback_days": source_lookback,
            "prism_concurrency": prism_concurrency,
            "partition": settings.slurm_partition,
            "account": settings.slurm_account,
            "status": "planned" if args.dry_run else "submitting",
            "maximum_attempts": 4,
            "initial_dependency": args.dependency,
        }
        if stream == "nrt":
            require_operational_gfs(settings.project_root)
            config = configuration(settings.project_root)
            plan["recent_nrt_gfs_requested"] = bool(config.get("enabled"))
            plan["recent_nrt_gfs_active"] = activation(settings.project_root)
            if plan["recent_nrt_gfs_active"]:
                recent_start = max(start, end - timedelta(days=config["lookback_days"] - 1))
                plan["recent_nrt_start"] = recent_start.isoformat()
                plan["recent_nrt_end"] = end.isoformat()
        if stream == 'nrt' and not args.revisions_only:
            plan['publication_ceiling'] = 'latest_available_contiguous_hour_at_execution'
            plan['daily_revision_continuation'] = args.cycle == 'daily'
            plan['window_dates_scope'] = 'revision workflow only; not latest-hour publication cutoff'
        print(json.dumps(plan, indent=2))
        if stream == "nrt" and not args.revisions_only:
            # Latest-hour publication has no dependency on optional-source refreshes.
            if any('|hrrr_download' in line for line in active):
                print('SKIP active HRRR acquisition; avoid concurrent canonical writers')
                return 0
            worker_partition = os.environ.get('HYDRO_OPS_NRT_PARTITION', 'compute-128')
            plan['latest_worker_partition'] = worker_partition
            command = ['sbatch', '--parsable', f'--partition={worker_partition}',
                       '--nodes=1', '--ntasks=1', '--cpus-per-task=128', '--tmp=240000',
                       '--time=06:00:00', f'--job-name=nwm-cycle-{args.cycle}-latest-hour',
                       f'--output={settings.log_root}/nrt-latest-extension-%j.out',
                       '--wrap', 'source bin/project_environment.sh; exec ' +
                       shlex.join([sys.executable, 'bin/run_latest_nrt.py'])]
            if settings.slurm_account:
                command.insert(1, f'--account={settings.slurm_account}')
            print(shlex.join(command))
            if args.dry_run:
                return 0
            job = subprocess.check_output(command, cwd=settings.project_root, text=True).strip().split(';')[0]
            plan.update(status='latest_extension_submitted', latest_extension_job_id=job)
            manifest = settings.work_root / f'nwm-latest-cycle-{args.cycle}-{job}.json'
            manifest.write_text(json.dumps(plan, indent=2)+'\n')
            if args.cycle == 'daily':
                follow = ['sbatch', '--parsable', f'--partition={settings.slurm_partition}',
                          '--nodes=1', '--ntasks=1', '--cpus-per-task=1', '--time=00:30:00',
                          f'--dependency=afterok:{job}', '--job-name=nrt-daily-revision-launch',
                          f'--output={settings.log_root}/nrt-daily-revision-launch-%j.out',
                          '--wrap', 'source bin/project_environment.sh; exec ' + shlex.join([
                              sys.executable, 'bin/update_nwm_forcing.py', '--cycle', 'daily', '--revisions-only'])]
                if settings.slurm_account:
                    follow.insert(1, f'--account={settings.slurm_account}')
                plan['revision_launcher_job_id'] = subprocess.check_output(
                    follow, cwd=settings.project_root, text=True).strip().split(';')[0]
            manifest.write_text(json.dumps(plan, indent=2)+'\n')
            print(json.dumps(plan, indent=2))
            return 0
        if args.dry_run:
            return 0
        refresh_output = ""
        if not args.skip_source_refresh:
            refresh = subprocess.run(
                [
                    sys.executable,
                    "bin/update_forcing.py",
                    "--repair-lookback-days",
                    str(source_lookback),
                ],
                cwd=settings.project_root,
                check=False,
                capture_output=True,
                text=True,
            )
            print(refresh.stdout, end="", flush=True)
            print(refresh.stderr, end="", file=sys.stderr, flush=True)
            if refresh.returncode:
                return refresh.returncode
            refresh_output = refresh.stdout
        source_names = {
            "nldas2_download",
            "stage4_download",
            "prism_download",
            "hrrr_download",
            "mrms_download",
        }
        active_source_ids = [
            job_id
            for line in active
            if "|" in line
            for job_id, job_name in [line.split("|", 1)]
            if job_name in source_names
        ]
        source_ids = list(
            dict.fromkeys(
                [*active_source_ids, *re.findall(r"Submitted batch job (\d+)", refresh_output)]
            )
        )
        if args.skip_source_refresh:
            source_ids = []
        plan["source_job_ids"] = source_ids
        baseline_start, baseline_end = start - timedelta(days=1), end + timedelta(days=1)
        if plan.get("recent_nrt_gfs_active"):
            baseline_end = recent_start  # Halo for the older PRISM window only.
        baseline_command = [
            sys.executable,
            "bin/submit_forcing_days.py",
            "--start",
            baseline_start.isoformat(),
            "--end",
            baseline_end.isoformat(),
            "--missing-only",
            "--max-concurrent",
            "16",
            "--cpus-per-task",
            "12",
            "--tmp-mb",
            "120000",
            "--job-name",
            f"nwm-cycle-{args.cycle}-baseline-{start:%Y%m%d}-{end:%Y%m%d}",
        ]
        no_retro_baseline_work = False
        if stream == "retro":
            missing_targets: list[date] = []
            day = start
            retro_root = forcing_stream_root(settings.project_root, "retro")
            while day <= end:
                output = retro_root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
                if not output.is_file():
                    missing_targets.append(day)
                day += timedelta(days=1)
            dependencies = sorted(
                {
                    target + timedelta(days=offset)
                    for target in missing_targets
                    for offset in (-1, 0, 1)
                }
            )
            if dependencies:
                baseline_command.extend(
                    ["--only-days", *(item.isoformat() for item in dependencies)]
                )
            else:
                no_retro_baseline_work = True
        if args.dependency:
            baseline_command.extend(["--dependency", args.dependency])
        elif source_ids:
            baseline_command.extend(["--dependency", f"afterany:{':'.join(source_ids)}"])
        baseline_output = "eligible_days=0\n" if no_retro_baseline_work else ""
        if not no_retro_baseline_work:
            baseline = subprocess.run(
                baseline_command,
                cwd=settings.project_root,
                check=False,
                capture_output=True,
                text=True,
            )
            print(baseline.stdout, end="", flush=True)
            print(baseline.stderr, end="", file=sys.stderr, flush=True)
            if baseline.returncode:
                return baseline.returncode
            baseline_output = baseline.stdout
        match = re.search(r"Submitted batch job (\d+)", baseline_output)
        baseline_id = match.group(1) if match else None
        plan["baseline_job_id"] = baseline_id
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        manifest = settings.work_root / f"nwm-forcing-cycle-{args.cycle}-{stamp}.json"
        plan["manifest"] = str(manifest.resolve())
        exports = (
            "ALL,"
            f"HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
            f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_CYCLE_MANIFEST={manifest.resolve()}"
        )
        command = [
            "sbatch",
            f"--partition={settings.slurm_partition}",
            f"--job-name=nwm-cycle-{args.cycle}-continue-{start:%Y%m%d}-{end:%Y%m%d}",
            "--cpus-per-task=1",
            "--time=48:00:00",
            "--requeue",
            f"--export={exports}",
            f"--output={settings.log_root}/nwm-cycle-{args.cycle}-%j.out",
            "slurm/converge_nwm_forcing_cycle.py",
        ]
        if settings.slurm_account:
            command.insert(2, f"--account={settings.slurm_account}")
        dependencies = [*source_ids, *([baseline_id] if baseline_id else [])]
        if dependencies:
            command.insert(2, f"--dependency=afterany:{':'.join(dependencies)}")
        plan["status"] = "submitted"
        manifest.write_text(json.dumps(plan, indent=2) + "\n")
        continued = subprocess.run(command, cwd=settings.project_root, check=False)
        return continued.returncode


if __name__ == "__main__":
    raise SystemExit(main())
