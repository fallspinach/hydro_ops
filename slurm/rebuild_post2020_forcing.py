#!/usr/bin/env python3
"""Rebuild an isolated batch, audit it, then replace published calendar days."""

from __future__ import annotations

import hashlib
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


def configure_writer(task: dict, project: Path, environ: dict) -> bool:
    """Opt in only frozen, benchmark-approved operational tasks."""
    receipt = task.get("writer_acceptance")
    if receipt is None:
        return False
    proof = json.loads(Path(receipt).read_text())
    if (proof.get("status") != "passed" or len(proof.get("days", [])) != 7
            or not task.get("writer_all_optimizations")):
        raise ValueError("Missing successful all-writer benchmark")
    stream = task["stream"]
    profile = task.get("writer_profile")
    if stream not in {"retro", "nrt"} or profile != (
            "validated_chunks_v1" if stream == "retro" else "reference"):
        raise ValueError("Unvalidated stream/writer profile")
    if Path(task["output_root"]).resolve() != (project / "forcing/outputs/conus" / stream).resolve():
        raise ValueError("Unexpected production output root")
    if environ.get("HYDRO_OPS_REBUILD_STATIC_ENVELOPE") != "1":
        raise ValueError("Static envelope required")
    enabled = "1" if stream == "retro" else "0"
    environ.update(HYDRO_OPS_ARCHIVE_CHUNKS=enabled, HYDRO_OPS_BENCH_FAST_MASK=enabled,
                   HYDRO_OPS_BENCH_MULTIDAY="0")
    for key in ("HYDRO_OPS_RETRO_NEW_PRODUCTION", "HYDRO_OPS_PRECIPITATION_CACHE"):
        environ.pop(key, None)
    return True


def finalize_static_manifest(source: Path, destination: Path) -> None:
    """Carry the validated candidate audit across the permanent-copy transaction."""
    manifest = source.with_name(source.name + ".manifest.json")
    record = json.loads(manifest.read_text())
    envelope = record["static_envelope"]
    # Caller checksum-verifies the permanent partial before its atomic rename.
    stat = destination.stat()
    envelope["staged_identity"] = envelope.pop("published_identity")
    envelope["published_identity"] = {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    envelope["audit_scope"] = "staged-candidate validation recorded in mask audit; permanent transfer checksum verified"
    record["daily_file"] = str(destination)
    target = destination.with_name(destination.name + ".manifest.json")
    temporary = target.with_name(target.name + ".part")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(temporary, target)


def dates(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ["HYDRO_OPS_PYTHON"]
    index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    task = json.loads(Path(os.environ["HYDRO_OPS_REBUILD_TASK_FILE"]).read_text().splitlines()[index])
    authorized = configure_writer(task, project, os.environ)
    if not authorized and any(os.environ.get(k) == '1' for k in ('HYDRO_OPS_BENCH_MULTIDAY', 'HYDRO_OPS_BENCH_FAST_MASK', 'HYDRO_OPS_ARCHIVE_CHUNKS')):
        Path(task['output_root']).resolve().relative_to((project/'forcing/work').resolve())
        if task['stream'] != 'retro':
            raise ValueError('Benchmark must not publish operational NRT baselines')
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
        if env.get('HYDRO_OPS_BENCH_MULTIDAY') == '1' and number % 3 == 0:
            cache = scratch / f'precipitation-cache-{number}'
            run(['bin/prepare_precipitation_cache.py', '--start', str(day),
                 '--days', str(min(3, len(baseline_days)-number)), '--output', str(cache),
                 '--work', str(scratch)])
            env['HYDRO_OPS_PRECIPITATION_CACHE'] = str(cache)
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
        if env.get('HYDRO_OPS_BENCH_MULTIDAY') == '1' and (number % 3 == 2 or number == len(baseline_days)-1):
            owned = Path(env.pop('HYDRO_OPS_PRECIPITATION_CACHE'))
            owned.relative_to(scratch)
            shutil.rmtree(owned)

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
        static_envelope = os.environ.get("HYDRO_OPS_REBUILD_STATIC_ENVELOPE") == "1"
        with Dataset(source, "r" if static_envelope else "r+") as data:
            t = data["time"]
            stamps = num2date(t[:], t.units, getattr(t, "calendar", "standard"))
            if [(v.year, v.month, v.day, v.hour) for v in stamps] != [
                (day.year, day.month, day.day, hour) for hour in range(24)
            ]:
                raise ValueError(f"Invalid calendar-day timestamps: {source}")
            if not str(getattr(data, "cnrfc_stage4_policy", "")):
                raise ValueError(f"Final publication lost CNRFC policy: {source}")
            if static_envelope:
                if (getattr(data, "forcing_recovery_policy", "") != POLICY
                        or getattr(data, "forcing_domain_policy", "") != "nldas2_seven_met_static_envelope_v4"
                        or getattr(data, "forcing_domain_content_audit", "") != "all_records_active_retained_unchanged_outside_envelope_missing_v4"):
                    raise ValueError(f"Final candidate lacks static-envelope acceptance: {source}")
            else:
                data.setncattr("forcing_recovery_policy", POLICY)
        destination = Path(task["output_root"]) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + f".rebuild-{os.environ['SLURM_JOB_ID']}.part")
        shutil.copy2(source, partial)
        if static_envelope:
            # Verify bytes before replacing the existing published daily file.
            record = json.loads(source.with_name(source.name + ".manifest.json").read_text())
            with partial.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != record["static_envelope"]["file_sha256"]:
                    raise ValueError(f"Static-envelope transfer checksum differs: {partial}")
        os.replace(partial, destination)
        manifest = source.with_suffix(source.suffix + ".manifest.json")
        if manifest.is_file():
            target = destination.with_suffix(destination.suffix + ".manifest.json")
            partial = target.with_suffix(target.suffix + ".part")
            shutil.copy2(manifest, partial)
            os.replace(partial, target)
        if static_envelope:
            finalize_static_manifest(source, destination)
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
