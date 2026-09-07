#!/usr/bin/env python3
"""Rewrite legacy project paths in JSON/JSONL manifests atomically."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

RELATIVE_MAPPINGS = (
    ("outputs/forcing/nwm", "forcing/outputs/conus"),
    ("outputs/forcing", "forcing/outputs"),
    ("outputs/wrf_hydro_tests", "nwm/outputs/tests"),
    ("outputs/inventory", "nwm/status/inventory"),
    ("outputs/status", "forcing/status"),
    ("data/static/nwm", "nwm/static"),
    ("data/static/dem", "forcing/static/dem"),
    ("data/static/hrrr", "forcing/static/hrrr"),
    ("data/static/nldas2", "forcing/static/nldas2"),
    ("data/static/prism", "forcing/static/prism"),
    ("data/static/remapping", "forcing/static/remapping"),
    ("data/static", "forcing/static"),
    ("data/model_inputs", "nwm/inputs/model_inputs"),
    ("data/observations", "nwm/inputs/observations"),
    ("data/forcing", "forcing/inputs"),
    ("work/nwm_subset_croton", "nwm/runs/nwm_subset_croton"),
    ("work/nwm_subset_mid_atlantic", "nwm/runs/nwm_subset_mid_atlantic"),
    ("work/wrf_hydro_daily_oracle", "nwm/runs/wrf_hydro_daily_oracle"),
    ("logs/wrf_hydro", "nwm/logs/wrf_hydro"),
    ("logs/nwm", "nwm/logs/nwm"),
    ("work", "forcing/work"),
    ("logs", "forcing/logs"),
)


def mappings(root: Path) -> tuple[tuple[str, str], ...]:
    absolute = tuple((str(root / old), str(root / new)) for old, new in RELATIVE_MAPPINGS)
    return absolute + RELATIVE_MAPPINGS


def rewrite_string(value: str, path_mappings: tuple[tuple[str, str], ...]) -> tuple[str, int]:
    for old, new in path_mappings:
        if value == old:
            return new, 1
        if value.startswith(old + "/"):
            return new + value[len(old) :], 1
    return value, 0


def rewrite_value(value: Any, path_mappings: tuple[tuple[str, str], ...]) -> tuple[Any, int]:
    if isinstance(value, str):
        return rewrite_string(value, path_mappings)
    if isinstance(value, list):
        changed = 0
        result = []
        for item in value:
            rewritten, count = rewrite_value(item, path_mappings)
            result.append(rewritten)
            changed += count
        return result, changed
    if isinstance(value, dict):
        changed = 0
        result = {}
        for key, item in value.items():
            rewritten, count = rewrite_value(item, path_mappings)
            result[key] = rewritten
            changed += count
        return result, changed
    return value, 0


def manifest_paths(root: Path) -> list[Path]:
    result: list[Path] = []
    structured_roots = (root / "forcing/work", root / "nwm/runs")
    for top in (root / "forcing", root / "nwm"):
        if not top.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(top, followlinks=False):
            dirnames[:] = [name for name in dirnames if not (Path(directory) / name).is_symlink()]
            result.extend(
                Path(directory) / name
                for name in filenames
                if name.endswith((".json", ".jsonl"))
                and (
                    "manifest" in name.lower()
                    or any(Path(directory).is_relative_to(base) for base in structured_roots)
                )
                and not (Path(directory) / name).is_symlink()
            )
    return sorted(result)


def load(path: Path) -> tuple[Any, bool]:
    if path.suffix == ".jsonl":
        lines = path.read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()], True
    return json.loads(path.read_text()), False


def render(value: Any, jsonl: bool) -> str:
    if jsonl:
        return "".join(json.dumps(item, sort_keys=True) + "\n" for item in value)
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def atomic_write(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--execute", action="store_true", help="write changes; default is a dry run")
    parser.add_argument("--workers", type=int, default=32, help="parallel manifest readers")
    args = parser.parse_args()
    root = args.project_root.resolve()
    path_mappings = mappings(root)
    planned: list[tuple[Path, str, int]] = []
    failures: list[dict[str, str]] = []
    scanned = 0

    paths = manifest_paths(root)
    scanned = len(paths)

    def inspect(path: Path) -> tuple[Path, str | None, int, str | None]:
        try:
            value, jsonl = load(path)
            rewritten, count = rewrite_value(value, path_mappings)
            if count:
                return path, render(rewritten, jsonl), count, None
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return path, None, 0, str(error)
        return path, None, 0, None

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for path, content, count, error in executor.map(inspect, paths):
            if error:
                failures.append({"path": str(path), "error": error})
            elif content is not None:
                planned.append((path, content, count))

    if failures:
        print(json.dumps({"scanned": scanned, "failures": failures}, indent=2))
        return 2
    if args.execute:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            list(executor.map(lambda item: atomic_write(item[0], item[1]), planned))

    report = {
        "mode": "execute" if args.execute else "dry-run",
        "scanned_files": scanned,
        "changed_files": len(planned),
        "changed_strings": sum(item[2] for item in planned),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
