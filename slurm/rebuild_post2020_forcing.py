#!/usr/bin/env python3
"""Rebuild an isolated batch, audit it, then replace published calendar days."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

configured_python = os.environ.get("HYDRO_OPS_PYTHON")
if configured_python and Path(sys.executable).resolve() != Path(configured_python).resolve():
    os.execv(configured_python, [configured_python, *sys.argv])

from netCDF4 import Dataset, num2date

POLICY = "cnrfc_prism_domain_rebuild_v1"


def dates(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ["HYDRO_OPS_PYTHON"]
    index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    task = json.loads(Path(os.environ["HYDRO_OPS_REBUILD_TASK_FILE"]).read_text().splitlines()[index])
    start, end = date.fromisoformat(task["start"]), date.fromisoformat(task["end"])
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    baseline = scratch / "rebuild_baseline"
    candidate = scratch / "rebuild_candidate" / task["stream"]
    env = dict(os.environ)

    def run(arguments: list[str], extra: dict[str, str] | None = None):
        subprocess.run([python, *arguments], cwd=project, env={**env, **(extra or {})}, check=True)

    scratch.mkdir(parents=True, exist_ok=True)
    baseline_days = list(dates(start - timedelta(days=1), end + timedelta(days=1)))
    day_file = scratch / "baseline_days.txt"
    day_file.write_text("".join(f"{day}\n" for day in baseline_days))
    for number, day in enumerate(baseline_days):
        run(["slurm/produce_forcing_day.py"], {
            "SLURM_ARRAY_TASK_ID": str(number),
            "HYDRO_OPS_FORCING_DAY_TASK_FILE": str(day_file),
            "HYDRO_OPS_OUTPUT_ROOT": str(baseline),
            "HYDRO_OPS_LAYOUT_ROOT": str(project),
            "HYDRO_OPS_ARCHIVE_DAILY": "1",
            "HYDRO_OPS_FORCE": "1",
        })
        path = baseline / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")
        with Dataset(path) as data:
            if not str(getattr(data, "cnrfc_stage4_policy", "")):
                raise ValueError(f"Baseline lacks CNRFC correction: {path}")

    calendar_task = scratch / "calendar_task.jsonl"
    calendar_task.write_text(json.dumps({
        **task, "baseline_root": str(baseline), "output_root": str(candidate),
    }) + "\n")
    run(["slurm/produce_prism_calendar_batch.py"], {
        "SLURM_ARRAY_TASK_ID": "0",
        "HYDRO_OPS_PRISM_CALENDAR_TASK_FILE": str(calendar_task),
    })
    # The calendar worker has already reread all eight fields after final repair.
    # Independently verify exact timestamps and required policy provenance here.
    for day in dates(start, end):
        relative = Path(day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1"))
        source = candidate / relative
        with Dataset(source, "r+") as data:
            t = data["time"]
            stamps = num2date(t[:], t.units, getattr(t, "calendar", "standard"))
            if [(v.year, v.month, v.day, v.hour) for v in stamps] != [
                (day.year, day.month, day.day, hour) for hour in range(24)
            ]:
                raise ValueError(f"Invalid calendar-day timestamps: {source}")
            if not str(getattr(data, "cnrfc_stage4_policy", "")):
                raise ValueError(f"Final publication lost CNRFC policy: {source}")
            data.setncattr("forcing_recovery_policy", POLICY)
        destination = Path(task["output_root"]) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + f".rebuild-{os.environ['SLURM_JOB_ID']}.part")
        shutil.copy2(source, partial)
        os.replace(partial, destination)
        manifest = source.with_suffix(source.suffix + ".manifest.json")
        if manifest.is_file():
            target = destination.with_suffix(destination.suffix + ".manifest.json")
            partial = target.with_suffix(target.suffix + ".part")
            shutil.copy2(manifest, partial)
            os.replace(partial, target)
        # Keep corrected NRT baselines for subsequent stable PRISM processing.
        if task["stream"] == "nrt":
            target = project / "forcing/outputs/conus/baseline" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("", ".manifest.json"):
                source_base = Path(str(baseline / relative) + suffix)
                target_base = Path(str(target) + suffix)
                partial = target_base.with_name(target_base.name + ".rebuild.part")
                shutil.copy2(source_base, partial)
                os.replace(partial, target_base)
        print(json.dumps({"day": str(day), "stream": task["stream"],
                          "status": "published", "policy": POLICY,
                          "path": str(destination)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
