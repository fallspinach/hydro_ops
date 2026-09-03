#!/usr/bin/env python3
"""Delete baseline daily archives only after accepted stable retro coverage exists."""

from __future__ import annotations

import argparse
import calendar
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.forcing.streams import baseline_root, forcing_stream_root


def forcing_path(root: Path, day: date) -> Path:
    return root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"


def accepted_retro(
    path: Path, *, frequency: str | None = None, allowed_records: tuple[int, ...] = (24,)
) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with Dataset(path) as data:
            actual_frequency = (
                str(data.getncattr("prism_constraint_frequency"))
                if "prism_constraint_frequency" in data.ncattrs()
                else "daily"
            )
            if frequency and actual_frequency != frequency:
                return False
            accepted = str(data.getncattr("prism_reconciliation_accepted")).lower() == "true"
            if not accepted or len(data.dimensions.get("time", ())) not in allowed_records:
                return False
            if actual_frequency == "daily":
                return data.getncattr("prism_precipitation_revision") == "stable"
            return actual_frequency == "monthly"
    except (OSError, AttributeError, KeyError):
        return False


def accepted_month(retro_root: Path, year: int, month: int) -> bool:
    stamp = f"{year:04d}{month:02d}"
    diagnostic = retro_root / f"{year:04d}" / f"{stamp}.monthly_prism_diagnostics.nc"
    try:
        with Dataset(diagnostic) as data:
            if str(data.getncattr("precipitation_accepted")).lower() != "true":
                return False
    except (OSError, AttributeError):
        return False
    return all(
        accepted_retro(
            forcing_path(retro_root, date(year, month, day)),
            frequency="monthly",
            allowed_records=(11,) if (year, month, day) == (1979, 1, 1) else (24,),
        )
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    project = args.project_root.resolve()
    baseline = baseline_root(project)
    retro = forcing_stream_root(project, "retro")
    counters: Counter[str] = Counter()
    bytes_removed = 0
    month_cache: dict[tuple[int, int], bool] = {}
    day = args.start
    while day <= args.end:
        source = forcing_path(baseline, day)
        legacy = source.with_suffix(f"{source.suffix}.nc")
        if not source.is_file() and legacy.is_file():
            source = legacy
        if not source.is_file():
            counters["baseline_missing"] += 1
            day += timedelta(days=1)
            continue
        retro_day = forcing_path(retro, day)
        frequency = None
        if accepted_retro(retro_day, frequency="monthly"):
            frequency = "monthly"
        elif accepted_retro(retro_day, frequency="daily"):
            frequency = "daily"
        if frequency == "monthly":
            key = (day.year, day.month)
            month_cache.setdefault(key, accepted_month(retro, *key))
            covered = month_cache[key]
        elif frequency == "daily":
            # Calendar publication has already combined both internal 12Z windows.
            covered = True
        else:
            covered = False
        if not covered:
            counters["retro_not_complete"] += 1
            day += timedelta(days=1)
            continue
        size = source.stat().st_size
        if not args.dry_run:
            source.unlink()
            source.with_suffix(source.suffix + ".manifest.json").unlink(missing_ok=True)
        counters["eligible" if args.dry_run else "deleted"] += 1
        bytes_removed += size
        day += timedelta(days=1)
    report = {
        "created": datetime.now(UTC).isoformat(),
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "baseline_root": str(baseline),
        "retro_root": str(retro),
        "dry_run": args.dry_run,
        "counts": dict(counters),
        "bytes_eligible_or_removed": bytes_removed,
    }
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
