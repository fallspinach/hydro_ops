#!/usr/bin/env python3
"""Move daily forcing chunks into the hourly product's existing year/month tree."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hydro_ops.config import load_settings


def move_collection(source: Path, destination: Path) -> int:
    moved = 0
    if not source.exists():
        return moved
    for path in sorted(source.rglob("*.nc")):
        relative = path.relative_to(source)
        target = destination / relative
        manifest = path.with_suffix(path.suffix + ".manifest.json")
        target_manifest = target.with_suffix(target.suffix + ".manifest.json")
        if target.exists() or target_manifest.exists():
            raise FileExistsError(f"Refusing to overwrite migrated archive: {target}")
        if not manifest.is_file():
            raise FileNotFoundError(f"Daily archive manifest is missing: {manifest}")
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        record = json.loads(manifest.read_text())
        record["daily_file"] = str(target)
        temporary = target_manifest.with_suffix(target_manifest.suffix + ".part")
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        temporary.replace(target_manifest)
        manifest.unlink()
        moved += 1
    for directory in sorted(source.rglob("*"), reverse=True):
        if directory.is_dir():
            directory.rmdir()
    source.rmdir()
    return moved


def main() -> int:
    settings = load_settings()
    counts = {
        "hrrr": move_collection(settings.hrrr_data_dir / "daily", settings.hrrr_data_dir),
        "mrms": move_collection(
            settings.mrms_data_dir / "daily", settings.mrms_data_dir / "netcdf"
        ),
        "stage4": move_collection(
            settings.stage4_data_dir / "daily", settings.stage4_data_dir / "netcdf"
        ),
    }
    for product, count in counts.items():
        print(f"{product}: moved {count} daily chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
