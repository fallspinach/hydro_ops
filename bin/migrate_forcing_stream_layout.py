#!/usr/bin/env python3
"""Migrate forcing outputs to nwm/{baseline,nrt,retro} after writers are idle."""

from __future__ import annotations

import argparse
import getpass
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

WRITER_PREFIXES = (
    "forcing-day",
    "forcing-production",
    "nwm-baseline",
    "nwm-nrt",
    "nwm-retro",
    "prism-nrt",
    "prism-retro",
    "prism-historical",
)


def active_writers() -> list[str]:
    result = subprocess.run(
        ["squeue", "--noheader", "--user", getpass.getuser(), "--format=%j"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        name
        for name in result.stdout.splitlines()
        if name and any(name.startswith(prefix) for prefix in WRITER_PREFIXES)
    )


def move_children(source: Path, destination: Path, *, excluded: set[str] | None = None) -> int:
    if not source.is_dir():
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for child in list(source.iterdir()):
        if excluded and child.name in excluded:
            continue
        target = destination / child.name
        if target.exists():
            raise FileExistsError(f"Migration target already exists: {target}")
        shutil.move(str(child), str(target))
        moved += 1
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--wait-seconds", type=int, default=14400)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    root = args.project_root.resolve()
    deadline = time.monotonic() + args.wait_seconds
    while writers := active_writers():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Forcing writers remained active: {writers}")
        print(f"waiting_for_writers={','.join(writers)}", flush=True)
        time.sleep(args.poll_seconds)

    nwm = root / "outputs/forcing/nwm"
    baseline = nwm / "baseline"
    moved = {
        "baseline_entries": move_children(
            nwm, baseline, excluded={"baseline", "nrt", "retro"}
        )
    }
    legacy_streams = root / "outputs/forcing/nwm_prism"
    for stream in ("nrt", "retro"):
        moved[f"{stream}_entries"] = move_children(legacy_streams / stream, nwm / stream)
        legacy = legacy_streams / stream
        if legacy.is_dir() and not any(legacy.iterdir()):
            legacy.rmdir()
    if legacy_streams.is_dir() and not any(legacy_streams.iterdir()):
        legacy_streams.rmdir()
    report = root / "outputs/forcing/nwm/layout_migration.json"
    report.write_text(
        json.dumps(
            {
                "completed": datetime.now(UTC).isoformat(),
                "layout": "outputs/forcing/nwm/{baseline,nrt,retro}",
                **moved,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
