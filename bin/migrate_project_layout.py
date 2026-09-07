#!/usr/bin/env python3
"""Atomically migrate the project into top-level forcing and nwm subprojects."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

WRITER_MARKERS = ("forcing", "nwm-retro", "nwm-nrt", "prism", "stage4", "hrrr", "mrms", "nldas")


def active_writers() -> list[str]:
    result = subprocess.run(
        ["squeue", "--noheader", "--user", getpass.getuser(), "--format=%j"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        name for name in result.stdout.splitlines() if any(marker in name.lower() for marker in WRITER_MARKERS)
    )


def rename(source: Path, destination: Path, actions: list[dict[str, str]], execute: bool) -> None:
    if not source.exists() or source.is_symlink():
        return
    if destination.exists() or destination.is_symlink():
        empty_directory = destination.is_dir() and not destination.is_symlink() and not any(destination.iterdir())
        if not empty_directory:
            raise FileExistsError(f"Migration destination already exists: {destination}")
        actions.append({"operation": "remove_empty_directory", "path": str(destination)})
        if execute:
            destination.rmdir()
    actions.append({"operation": "rename", "source": str(source), "destination": str(destination)})
    if execute:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)


def link(path: Path, target: str, actions: list[dict[str, str]], execute: bool) -> None:
    if path.is_symlink():
        if os.readlink(path) != target:
            raise RuntimeError(f"Unexpected compatibility link: {path} -> {os.readlink(path)}")
        return
    scheduled_away = any(item.get("operation") == "rename" and item.get("source") == str(path) for item in actions)
    if path.exists() and not (not execute and scheduled_away):
        raise FileExistsError(f"Compatibility-link path is occupied: {path}")
    actions.append({"operation": "symlink", "path": str(path), "target": target})
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target, target_is_directory=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--execute", action="store_true", help="perform the migration; default is a dry run")
    parser.add_argument(
        "--create-compatibility-links",
        action="store_true",
        help="create legacy path symlinks (disabled by default)",
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    writers = active_writers()
    if writers:
        raise RuntimeError(f"Refusing migration while writer jobs are active: {writers}")

    actions: list[dict[str, str]] = []
    forcing = root / "forcing"
    nwm = root / "nwm"

    rename(root / "data/forcing", forcing / "inputs", actions, args.execute)
    for child in sorted((root / "data/static").iterdir() if (root / "data/static").is_dir() else []):
        if child.name != "nwm":
            rename(child, forcing / "static" / child.name, actions, args.execute)
    rename(root / "data/static/nwm", nwm / "static", actions, args.execute)
    rename(root / "data/model_inputs", nwm / "inputs/model_inputs", actions, args.execute)
    rename(root / "data/observations", nwm / "inputs/observations", actions, args.execute)

    rename(root / "outputs/forcing", forcing / "outputs", actions, args.execute)
    rename(forcing / "outputs/nwm", forcing / "outputs/conus", actions, args.execute)
    rename(root / "outputs/status", forcing / "status", actions, args.execute)
    rename(root / "outputs/inventory", nwm / "status/inventory", actions, args.execute)
    rename(root / "outputs/wrf_hydro_tests", nwm / "outputs/tests", actions, args.execute)

    rename(root / "work", forcing / "work", actions, args.execute)
    for name in ("nwm_subset_croton", "nwm_subset_mid_atlantic", "wrf_hydro_daily_oracle"):
        rename(forcing / "work" / name, nwm / "runs" / name, actions, args.execute)

    rename(root / "logs", forcing / "logs", actions, args.execute)
    for name in ("nwm", "wrf_hydro"):
        rename(forcing / "logs" / name, nwm / "logs" / name, actions, args.execute)

    if args.create_compatibility_links:
        link(root / "data/forcing", "../forcing/inputs", actions, args.execute)
        for name in ("dem", "hrrr", "nldas2", "prism", "remapping"):
            link(root / "data/static" / name, f"../../forcing/static/{name}", actions, args.execute)
        link(root / "data/static/nwm", "../../nwm/static", actions, args.execute)
        link(root / "data/model_inputs", "../nwm/inputs/model_inputs", actions, args.execute)
        link(root / "data/observations", "../nwm/inputs/observations", actions, args.execute)
        link(root / "outputs/forcing", "../forcing/outputs", actions, args.execute)
        link(forcing / "outputs/nwm", "conus", actions, args.execute)
        link(root / "outputs/status", "../forcing/status", actions, args.execute)
        link(root / "outputs/inventory", "../nwm/status/inventory", actions, args.execute)
        link(root / "outputs/wrf_hydro_tests", "../nwm/outputs/tests", actions, args.execute)
        link(root / "work", "forcing/work", actions, args.execute)
        link(root / "logs", "forcing/logs", actions, args.execute)
        for name in ("nwm_subset_croton", "nwm_subset_mid_atlantic", "wrf_hydro_daily_oracle"):
            link(forcing / "work" / name, f"../../nwm/runs/{name}", actions, args.execute)
        for name in ("nwm", "wrf_hydro"):
            link(forcing / "logs" / name, f"../../nwm/logs/{name}", actions, args.execute)

    report = {
        "schema_version": 1,
        "mode": "execute" if args.execute else "dry-run",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "project_root": str(root),
        "actions": actions,
    }
    if args.execute:
        destination = forcing / "status/project-layout-migration.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        temporary.replace(destination)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
