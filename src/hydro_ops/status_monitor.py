"""Fast, read-only inventory of forcing data and operational workflows."""

from __future__ import annotations

import getpass
import json
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from hydro_ops.config import Settings
from hydro_ops.forcing_status import forcing_coverage

SCHEMA_VERSION = "1.0"
DAY_FILE = re.compile(r"^(\d{8})\.LDASIN_DOMAIN1$")


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _runs(days: list[date]) -> list[dict[str, Any]]:
    if not days:
        return []
    answer: list[dict[str, Any]] = []
    first = previous = days[0]
    for item in days[1:]:
        if item != previous + timedelta(days=1):
            answer.append(
                {
                    "start": first.isoformat(),
                    "end": previous.isoformat(),
                    "days": (previous - first).days + 1,
                }
            )
            first = item
        previous = item
    answer.append(
        {
            "start": first.isoformat(),
            "end": previous.isoformat(),
            "days": (previous - first).days + 1,
        }
    )
    return answer


def production_inventory(
    root: Path, *, start: date | None = None, end: date | None = None, gap_limit: int = 20
) -> dict[str, Any]:
    """Inventory published daily files using names and metadata, not NetCDF reads."""
    paths: dict[date, list[Path]] = {}
    bytes_total = 0
    partial_files = 0
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name.endswith(".part"):
                partial_files += 1
                continue
            match = DAY_FILE.match(path.name)
            if not match:
                continue
            day = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=UTC).date()
            if (start and day < start) or (end and day > end):
                continue
            paths.setdefault(day, []).append(path)
            bytes_total += path.stat().st_size
    days = sorted(paths)
    range_start = start or (days[0] if days else None)
    range_end = end or (days[-1] if days else None)
    missing: list[date] = []
    if range_start and range_end and range_start <= range_end:
        cursor = range_start
        present = set(days)
        while cursor <= range_end:
            if cursor not in present:
                missing.append(cursor)
            cursor += timedelta(days=1)
    return {
        "root": str(root.resolve()),
        "first_day": _iso(days[0] if days else None),
        "last_day": _iso(days[-1] if days else None),
        "unique_days": len(days),
        "files": sum(len(group) for group in paths.values()),
        "bytes": bytes_total,
        "duplicate_days": [item.isoformat() for item in days if len(paths[item]) > 1],
        "partial_files": partial_files,
        "coverage_segments": _runs(days),
        "audit_window": {"start": _iso(range_start), "end": _iso(range_end)},
        "missing_days": len(missing),
        "missing_day_examples": [item.isoformat() for item in missing[:gap_limit]],
        "missing_days_truncated": len(missing) > gap_limit,
    }


def slurm_inventory(user: str | None = None) -> dict[str, Any]:
    """Return current jobs; remain useful on hosts without SLURM commands."""
    owner = user or getpass.getuser()
    try:
        result = subprocess.run(
            ["squeue", "--noheader", "--user", owner, "--format=%A|%T|%j|%M|%l|%D|%C|%R"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        return {"available": False, "error": str(error), "jobs": [], "job_count": 0, "states": {}}
    jobs: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split("|", 7)
        if len(fields) != 8:
            continue
        job_id, state, name, elapsed, limit, nodes, cpus, reason = fields
        jobs.append(
            {
                "job_id": job_id,
                "state": state,
                "name": name,
                "elapsed": elapsed,
                "time_limit": limit,
                "nodes": int(nodes),
                "cpus": int(cpus),
                "reason_or_node": reason,
            }
        )
    states: dict[str, int] = {}
    for job in jobs:
        states[job["state"]] = states.get(job["state"], 0) + 1
    return {
        "available": True,
        "error": None,
        "jobs": jobs,
        "job_count": len(jobs),
        "states": states,
    }


def coordinator_inventory(work_root: Path, limit: int = 10) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    invalid: list[str] = []
    paths = sorted(work_root.glob("nwm-forcing-cycle-*.json"), reverse=True)[:limit]
    for path in paths:
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            invalid.append(str(path))
            continue
        records.append(
            {
                key: record.get(key)
                for key in (
                    "created",
                    "cycle",
                    "stream",
                    "start",
                    "end",
                    "status",
                    "source_job_ids",
                    "baseline_job_id",
                    "prism_job_id",
                    "cleanup_job_id",
                )
            }
            | {"path": str(path.resolve())}
        )
    return {"recent": records, "invalid_manifests": invalid}


def build_status(
    settings: Settings,
    *,
    now: datetime | None = None,
    start: date | None = None,
    end: date | None = None,
    gap_limit: int = 20,
    include_slurm: bool = True,
) -> dict[str, Any]:
    generated = now or datetime.now(UTC)
    external = []
    for row in forcing_coverage(settings):
        age = ((generated - row.latest).total_seconds() / 3600) if row.latest else None
        external.append(
            {
                "product": row.product,
                "latest_valid_utc": _iso(row.latest),
                "age_hours": round(max(0.0, age), 2) if age is not None else None,
                "files": row.files,
                "status": "available" if row.latest else "missing",
            }
        )
    nwm_root = settings.output_root / "forcing" / "nwm"
    production = {
        stream: production_inventory(nwm_root / stream, start=start, end=end, gap_limit=gap_limit)
        for stream in ("baseline", "nrt", "retro")
    }
    issues = []
    for stream, item in production.items():
        if item["partial_files"]:
            issues.append(f"{stream}: {item['partial_files']} partial file(s)")
        if item["duplicate_days"]:
            issues.append(f"{stream}: {len(item['duplicate_days'])} duplicate day(s)")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated.isoformat(),
        "project_root": str(settings.project_root.resolve()),
        "summary": {"status": "attention" if issues else "ok", "issues": issues},
        "external_sources": external,
        "production_streams": production,
        "slurm": slurm_inventory() if include_slurm else {"available": False, "skipped": True},
        "coordinators": coordinator_inventory(settings.work_root),
        "scan": {"mode": "metadata", "netcdf_contents_validated": False},
    }


def _size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if amount < 1024 or unit == "PiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Forcing status — {report['generated_at_utc']}",
        f"Overall: {report['summary']['status'].upper()}",
        "",
        "External sources",
        f"{'Product':<25} {'Latest valid UTC':<25} {'Age':>10} {'Files':>10}",
        "-" * 74,
    ]
    for row in report["external_sources"]:
        age = "-" if row["age_hours"] is None else f"{row['age_hours']:.1f} h"
        lines.append(
            f"{row['product']:<25} {(row['latest_valid_utc'] or 'missing'):<25} {age:>10} {row['files']:>10,d}"
        )
    lines.extend(
        [
            "",
            "NWM daily production streams",
            f"{'Stream':<10} {'First':<12} {'Last':<12} {'Days':>8} {'Missing':>9} {'Size':>11}",
            "-" * 68,
        ]
    )
    for name, item in report["production_streams"].items():
        lines.append(
            f"{name:<10} {(item['first_day'] or '-'):<12} {(item['last_day'] or '-'):<12} {item['unique_days']:>8,d} {item['missing_days']:>9,d} {_size(item['bytes']):>11}"
        )
    slurm = report["slurm"]
    lines.extend(["", "SLURM"])
    if not slurm.get("available"):
        lines.append(f"unavailable: {slurm.get('error', 'scan skipped')}")
    else:
        states = ", ".join(f"{key}={value}" for key, value in sorted(slurm["states"].items()))
        lines.append(f"jobs={slurm['job_count']}" + (f" ({states})" if states else ""))
        for job in slurm["jobs"]:
            lines.append(
                f"  {job['job_id']:<10} {job['state']:<9} {job['elapsed']:<11} {job['name']}"
            )
    if report["summary"]["issues"]:
        lines.extend(["", "Attention"] + [f"  - {issue}" for issue in report["summary"]["issues"]])
    return "\n".join(lines)
