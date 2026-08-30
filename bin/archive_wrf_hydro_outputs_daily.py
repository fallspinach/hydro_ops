#!/usr/bin/env python3
"""Aggregate hourly WRF-Hydro NetCDF outputs into verified daily collections."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

HOURLY_RE = re.compile(
    r"^(?P<day>\d{8})(?P<hour>\d{2})(?P<minute>\d{2})?\."
    r"(?P<product>[A-Z0-9_]+DOMAIN\d+)$"
)


def group_hourly_outputs(directory: Path) -> dict[tuple[str, str], list[Path]]:
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path in directory.iterdir():
        match = HOURLY_RE.match(path.name)
        if match:
            groups[(match.group("day"), match.group("product"))].append(path)
    return {key: sorted(paths) for key, paths in groups.items()}


def record_count(path: Path) -> int:
    result = subprocess.run(
        ["ncks", "--trd", "-m", "-M", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        match = re.search(r"(?:name = time|time: type = .*?), size = (\d+)", line)
        if match:
            return int(match.group(1))
    raise RuntimeError(f"Could not determine time dimension in {path}")


def archive_group(
    paths: list[Path], output: Path, expected_records: int, compression_level: int
) -> None:
    if len(paths) != expected_records:
        raise RuntimeError(
            f"Expected {expected_records} hourly files for {output.name}, found {len(paths)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.partial")
    subprocess.run(
        [
            "ncrcat",
            "-O",
            "-4",
            "-L",
            str(compression_level),
            *map(str, paths),
            str(temporary),
        ],
        check=True,
    )
    if record_count(temporary) != expected_records:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Daily collection failed record-count validation: {output}")
    temporary.replace(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing hourly model outputs")
    parser.add_argument("--output-dir", type=Path, help="Destination; defaults to the input directory")
    parser.add_argument("--day", help="Only archive YYYYMMDD")
    parser.add_argument("--product", action="append", help="Only archive this output product")
    parser.add_argument("--expected-records", type=int, default=24)
    parser.add_argument("--compression-level", type=int, choices=range(10), default=2)
    parser.add_argument(
        "--remove-hourly",
        action="store_true",
        help="Delete inputs only after the daily collection passes validation",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not shutil.which("ncrcat") or not shutil.which("ncks"):
        raise SystemExit("ncrcat and ncks are required")
    destination = args.output_dir or args.directory
    products = set(args.product or [])
    archived = 0
    for (day, product), paths in sorted(group_hourly_outputs(args.directory).items()):
        if args.day and day != args.day:
            continue
        if products and product not in products:
            continue
        output = destination / f"{day}.{product}"
        archive_group(paths, output, args.expected_records, args.compression_level)
        if args.remove_hourly:
            for path in paths:
                path.unlink()
        archived += 1
        print(f"archived {len(paths)} records -> {output}")
    if archived == 0:
        raise SystemExit("No complete matching output groups were selected")


if __name__ == "__main__":
    main()
