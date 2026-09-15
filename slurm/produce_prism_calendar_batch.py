#!/usr/bin/env python3
# SBATCH --nodes=1
# SBATCH --ntasks=1
# SBATCH --time=48:00:00
"""Produce a contiguous batch of PRISM-constrained UTC calendar days."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

configured_python = os.environ.get("HYDRO_OPS_PYTHON")
if configured_python and Path(sys.executable).resolve() != Path(configured_python).resolve():
    os.execv(configured_python, [configured_python, *sys.argv])

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.retro_publication import check_scratch, complete


def run(command: list[str], project: Path) -> None:
    completed = subprocess.run(command, cwd=project, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def validate_publication(path: Path, day: date) -> None:
    """Fail closed before allowing stable baseline retention cleanup."""
    with Dataset(path) as data:
        time = data["time"]
        values = num2date(
            time[:],
            time.units,
            calendar=getattr(time, "calendar", "standard"),
            only_use_cftime_datetimes=False,
            only_use_python_datetimes=True,
        )
        checks = {
            "utc_hour_sequence": [(v.date(), v.hour, v.minute, v.second) for v in values]
            == [(day, hour, 0, 0) for hour in range(24)],
            "archive_granularity": str(getattr(data, "archive_granularity", "")) == "utc_calendar_day",
            "prism_reconciliation_accepted": str(getattr(data, "prism_reconciliation_accepted", "false")).lower() == "true",
            "forcing_domain_policy": str(getattr(data, "forcing_domain_policy", "")) == "nldas2_active_gaps_preserve_inactive_v3",
            "forcing_domain_content_audit": str(getattr(data, "forcing_domain_content_audit", "")) == "all_records_active_complete_outside_masked_inactive_preserved_v3",
        }
        if day >= date(2020, 7, 1):
            checks["cnrfc_stage4_policy"] = bool(getattr(data, "cnrfc_stage4_policy", ""))
        attributes = {key: str(getattr(data, key, "<missing>")) for key in checks if key != "utc_hour_sequence"}
    if not all(checks.values()):
        diagnostic = {"path": str(path), "failed_checks": [k for k, ok in checks.items() if not ok],
                      "attributes": attributes}
        print(json.dumps({"status": "publication_rejected", **diagnostic}), flush=True)
        raise ValueError(f"Refusing baseline cleanup after invalid publication: {diagnostic}")


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    task_file = Path(os.environ["HYDRO_OPS_PRISM_CALENDAR_TASK_FILE"])
    task = json.loads(task_file.read_text().splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])])
    writer_profile = task.get("writer_profile")
    if writer_profile is not None:
        if writer_profile not in {"validated_chunks_v1", "reference"}:
            raise ValueError(f"Unknown writer profile: {writer_profile}")
        enabled = "1" if writer_profile == "validated_chunks_v1" else "0"
        os.environ.update(HYDRO_OPS_ARCHIVE_CHUNKS=enabled, HYDRO_OPS_BENCH_FAST_MASK=enabled)
        print(json.dumps({"writer_profile": writer_profile,
                          "chunk_archives": enabled == "1", "fast_static_mask": enabled == "1"}), flush=True)
    start = date.fromisoformat(task["start"])
    end = date.fromisoformat(task["end"])
    stream = task["stream"]
    revision = task["revision"]
    baseline = Path(task["baseline_root"])
    destination = Path(task["output_root"])
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    staging = scratch / "prism_windows" / stream
    staging.mkdir(parents=True, exist_ok=True)
    check_scratch(scratch)
    new_production = os.environ.get("HYDRO_OPS_RETRO_NEW_PRODUCTION") == "1"
    calendar_root = scratch / "calendar" / stream if new_production else destination

    def produce_window(day: date) -> None:
        run(
            [
                python,
                str(project / "bin/produce_prism_constrained_daily.py"),
                "--day",
                day.isoformat(),
                "--complete-root",
                str(baseline),
                "--output-root",
                str(staging),
                "--revision",
                revision,
                "--stream",
                stream,
                "--work-directory",
                str(scratch),
                "--archive-access",
                "direct",
                "--allow-legacy-12utc-output",
            ],
            project,
        )

    produce_window(start)
    day = start
    while day <= end:
        produce_window(day + timedelta(days=1))
        run(
            [
                python,
                str(project / "bin/materialize_calendar_forcing.py"),
                "--input-root",
                str(staging),
                "--output-root",
                str(calendar_root),
                "--start",
                day.isoformat(),
                "--days",
                "1",
                "--stream",
                stream,
                "--require-accepted-prism-windows",
                "--hierarchical",
                "--work-directory",
                str(scratch),
            ],
            project,
        )
        publication = calendar_root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        run(
            [
                python,
                str(project / "bin/repair_nwm_forcing_domain.py"),
                str(publication),
                "--in-place",
                "--active-gaps-only",
                "--work-directory",
                str(scratch),
            ],
            project,
        )
        validate_publication(publication, day)
        if os.environ.get("HYDRO_OPS_REBUILD_STATIC_ENVELOPE") == "1":
            # Applied after PRISM and v3 active-cell repair, never before either.
            # Freeze recovery metadata before computing the static-mask checksum.
            with Dataset(publication, "r+") as data:
                if writer_profile is not None:
                    data.forcing_writer_profile = writer_profile
                data.forcing_recovery_policy = (
                    "retro_prism_static_production_v1" if new_production else "cnrfc_prism_domain_rebuild_v1"
                )
            run([
                python, str(project / "bin/apply_static_forcing_mask.py"),
                "--path", str(publication),
                "--mask", str(project / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"),
                "--root", str(calendar_root), "--work", str(scratch),
                "--state", str(project / "forcing/work/post2020-static-envelope-audits" / os.environ["SLURM_JOB_ID"] / stream),
                *( ["--staged-production", "--publish-to",
                    str(destination / publication.relative_to(calendar_root))]
                   if new_production else ["--staged-rebuild"] ),
                *(['--fast'] if os.environ.get('HYDRO_OPS_BENCH_FAST_MASK') == '1' else []),
            ], project)
        if new_production:
            final = destination / publication.relative_to(calendar_root)
            if not complete(final):
                raise ValueError(f"Final publication failed strict completion check: {final}")
            publication.unlink()
            publication.with_name(publication.name + ".manifest.json").unlink(missing_ok=True)
        stamp = day.strftime("%Y%m%d")
        for path in (staging / day.strftime("%Y/%m")).glob(f"{stamp}.*"):
            path.unlink()
        day += timedelta(days=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
