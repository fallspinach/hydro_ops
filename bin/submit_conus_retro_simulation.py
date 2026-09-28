#!/usr/bin/env python3
"""Submit a two-day acceptance test and a bounded, dependent annual run chain."""

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path


def verify_completed_predecessor(project, campaign, year, job):
    """Require accounting success and the accepted terminal restart pair."""
    from hydro_ops.wrf_hydro.production import restart_check, restart_names

    result = subprocess.check_output(
        ["sacct", "-j", str(job), "-X", "-n", "-P", "--format=JobID,State,ExitCode"],
        text=True,
    )
    rows = [line.strip().split("|") for line in result.splitlines()]
    if not any(row[:3] == [str(job), "COMPLETED", "0:0"] for row in rows):
        raise RuntimeError(f"Predecessor {job} has no successful accounting record")
    root = project / "nwm/runs/conus/retro" / campaign
    accepted = json.loads((root / f"{year}-passed.json").read_text())
    end = datetime(year + 1, 1, 1)  # noqa: DTZ001 -- model clocks are naive UTC
    if accepted.get("passed") is not True or accepted.get("end") != str(end):
        raise RuntimeError("Predecessor annual acceptance is missing or inconsistent")
    restart_root = project / "nwm/restarts/conus/retro" / campaign / "production"
    paths = [restart_root / end.strftime("%Y/%m") / name for name in restart_names(end)]
    if accepted.get("restarts") != [str(path) for path in paths]:
        raise RuntimeError("Predecessor restart paths do not match the campaign")
    restart_check(paths, end)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--end-year", type=int, default=1980)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--extend", action="store_true", help="Append years to an existing chain")
    parser.add_argument("--completed-predecessor", action="store_true",
                        help="Verify completed predecessor and restart pair instead of live dependency")
    args = parser.parse_args()
    if args.completed_predecessor and not args.extend:
        parser.error("--completed-predecessor requires --extend")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.campaign):
        parser.error("Invalid campaign name")
    if not 1979 <= args.end_year <= 2002:
        parser.error("Supported production range is 1979-2002")
    project = Path(__file__).resolve().parents[1]
    logs = project / "nwm/logs/wrf_hydro/conus/retro" / args.campaign
    if args.submit:
        logs.mkdir(parents=True, exist_ok=True)
    dependency = None
    records = []
    manifest = logs / "submission.json"
    years = [None, *range(1979, args.end_year + 1)]
    if args.extend:
        records = json.loads(manifest.read_text())
        last = records[-1]
        command = last["command"]
        if "--year" not in command:
            parser.error("Existing chain must end in an annual job")
        last_year = int(command[command.index("--year") + 1])
        dependency = last["job"]
        if args.completed_predecessor:
            verify_completed_predecessor(project, args.campaign, last_year, dependency)
            dependency = None
        years = list(range(last_year + 1, args.end_year + 1))
        if not years:
            parser.error("Requested end year is already covered")
    elif manifest.exists():
        parser.error("Campaign already submitted; use --extend to append years")
    for year in years:
        label = "acceptance-19790102-19790104" if year is None else f"{year}-monthly-checkpoints"
        command = [
            "sbatch",
            "--parsable",
            f"--job-name=wrfh-conus-retro-{label}",
            f"--output={logs}/%x-%j.out",
            f"--chdir={project}",
        ]
        if dependency:
            command += [f"--dependency=afterok:{dependency}"]
        if year is None:
            command += ["--time=04:00:00"]
        command += [
            str(project / "slurm/run_wrf_hydro_conus_production.sh"),
            "--campaign",
            args.campaign,
        ]
        command += ["--test"] if year is None else ["--year", str(year)]
        if args.submit:
            dependency = (
                subprocess.check_output(command, text=True, cwd=project).strip().split(";")[0]
            )
        else:
            dependency = f"JOB_{len(records)}"
        records.append({"job": dependency, "command": command})
        print(json.dumps(records[-1]), flush=True)
        if args.submit:
            temporary = manifest.with_suffix(".json.partial")
            temporary.write_text(json.dumps(records, indent=2) + "\n")
            temporary.replace(manifest)


if __name__ == "__main__":
    main()
