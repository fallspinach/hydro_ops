#!/usr/bin/env python3
"""Remove hourly and raw forcing artifacts protected by verified daily archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def daily_files(root: Path) -> list[Path]:
    patterns = (
        "hrrr_forcing.????????.nc",
        "mrms_*.????????.nc",
        "stage4_*_01h.????????.nc",
    )
    return sorted({path for pattern in patterns for path in root.rglob(pattern)})


def day_from_name(path: Path) -> date:
    stamp = path.stem.rsplit(".", 1)[-1]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))


def raw_sources(hourly: list[Path], daily: Path, day: date) -> list[Path]:
    values: set[Path] = set()
    text = str(daily)
    if daily.name.startswith("hrrr_forcing"):
        values.update(Path(str(path).removesuffix(".nc")) for path in hourly)
    elif daily.name.startswith("mrms_"):
        for path in hourly:
            values.add(Path(str(path).replace("/netcdf/", "/raw/")).with_suffix(".gz"))
    elif "stage4_realtime" in daily.name:
        for path in hourly:
            values.add(Path(str(path).replace("/netcdf/realtime/", "/realtime/")).with_suffix(""))
    elif "stage4_archive" in daily.name:
        stage_root = Path(text.split("/netcdf/archive/", 1)[0])
        values.add(stage_root / "archive" / day.strftime("%Y/%m") / f"ST4.{day:%Y%m%d}.tar")
    return sorted(path for path in values if path.is_file())


def inspect(daily: Path, cutoff: date) -> dict:
    day = day_from_name(daily)
    manifest_path = daily.with_suffix(daily.suffix + ".manifest.json")
    record = json.loads(manifest_path.read_text())
    if record.get("verified") is not True or record.get("day") != day.isoformat():
        raise ValueError(f"Unverified or mismatched manifest: {manifest_path}")
    hourly = [Path(item["path"]) for item in record["source_files"]]
    if record.get("cleanup_state") == "complete":
        return {
            "day": day,
            "eligible": False,
            "cleaned": True,
            "daily": daily,
            "manifest": manifest_path,
            "record": record,
            "hourly": [],
            "raw": [],
            "bytes": 0,
        }
    if record.get("cleanup_state") == "in_progress":
        raw = [Path(value) for value in record.get("cleanup_raw_sources", [])]
        return {
            "day": day,
            "eligible": day <= cutoff,
            "cleaned": False,
            "daily": daily,
            "manifest": manifest_path,
            "record": record,
            "hourly": hourly,
            "raw": raw,
            "bytes": sum(path.stat().st_size for path in [*hourly, *raw] if path.exists()),
        }
    for path, item in zip(hourly, record["source_files"], strict=True):
        if not path.is_file():
            raise FileNotFoundError(f"Manifest source is missing: {path}")
        if path.stat().st_size != item["bytes"] or path.stat().st_mtime != item["mtime"]:
            raise ValueError(f"Manifest source changed after aggregation: {path}")
    raw = raw_sources(hourly, daily, day)
    return {
        "day": day,
        "eligible": day <= cutoff,
        "cleaned": False,
        "daily": daily,
        "manifest": manifest_path,
        "record": record,
        "hourly": hourly,
        "raw": raw,
        "bytes": sum(path.stat().st_size for path in [*hourly, *raw]),
    }


def remove_empty_parents(paths: list[Path], stop_roots: set[Path]) -> None:
    for path in paths:
        parent = path.parent
        while parent not in stop_roots and parent != parent.parent:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/forcing/noaa"))
    parser.add_argument("--retention-days", type=int, default=31)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--report", type=Path, default=Path("outputs/inventory/forcing_cleanup.json")
    )
    args = parser.parse_args()
    if args.retention_days < 0:
        parser.error("--retention-days cannot be negative")
    cutoff = datetime.now(UTC).date() - timedelta(days=args.retention_days)
    candidates = [inspect(path, cutoff) for path in daily_files(args.root)]
    eligible = [item for item in candidates if item["eligible"]]
    report = {
        "created": datetime.now(UTC).isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        "retention_days": args.retention_days,
        "cutoff_inclusive": cutoff.isoformat(),
        "daily_archives_scanned": len(candidates),
        "eligible_daily_archives": len(eligible),
        "previously_cleaned_daily_archives": sum(item["cleaned"] for item in candidates),
        "hourly_files": sum(len(item["hourly"]) for item in eligible),
        "raw_files": sum(len(item["raw"]) for item in eligible),
        "bytes": sum(item["bytes"] for item in eligible),
    }
    if args.apply:
        removed: list[Path] = []
        for item in eligible:
            record = item["record"]
            record.update(
                daily_sha256=sha256(item["daily"]),
                cleanup_state="in_progress",
                cleanup_raw_sources=[str(path) for path in item["raw"]],
            )
            temporary = item["manifest"].with_suffix(item["manifest"].suffix + ".part")
            temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
            temporary.replace(item["manifest"])
            for path in [*item["hourly"], *item["raw"]]:
                path.unlink(missing_ok=True)
                removed.append(path)
            record.update(
                cleanup_state="complete",
                cleanup_time=datetime.now(UTC).isoformat(),
                hourly_sources_removed=True,
                raw_sources_removed=record.pop("cleanup_raw_sources"),
            )
            temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
            temporary.replace(item["manifest"])
        remove_empty_parents(removed, {args.root})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(args.report.suffix + ".part")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
