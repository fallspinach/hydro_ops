#!/usr/bin/env python3
"""Resumably compress existing HRRR, MRMS, and Stage-IV NetCDF files in place."""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import sys
from pathlib import Path

from hydro_ops.download.netcdf_compression import compress_netcdf, is_compressed_netcdf

LOG = logging.getLogger("compress_forcing_netcdf")
DEFAULT_ROOTS = (
    Path("data/forcing/noaa/hrrr"),
    Path("data/forcing/noaa/mrms/conus/1km/hourly/netcdf"),
    Path("data/forcing/noaa/stage4/netcdf"),
)


def candidates(roots: list[Path]) -> list[Path]:
    transient_markers = (".part", ".compressing", ".repacked", ".wgrib2.nc")
    return sorted(
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*.nc")
        if not any(marker in path.name for marker in transient_markers)
    )


def compress_one(path: Path, level: int) -> str:
    if is_compressed_netcdf(path):
        return "skipped"
    temporary = path.with_name(f"{path.name}.repacked")
    temporary.unlink(missing_ok=True)
    try:
        compress_netcdf(path, temporary, level=level)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return "compressed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", type=Path, default=list(DEFAULT_ROOTS))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--level", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    paths = candidates(args.roots)
    LOG.info("Found %d stable NetCDF candidates; checking compression headers", len(paths))
    pending = [path for path in paths if not is_compressed_netcdf(path)]
    LOG.info("Found %d NetCDF files; %d require compression", len(paths), len(pending))
    if args.dry_run:
        return 0
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(compress_one, path, args.level): path for path in pending}
        for future in concurrent.futures.as_completed(futures):
            path = futures[future]
            try:
                completed += future.result() == "compressed"
            except Exception:
                LOG.exception("Failed to compress %s", path)
                return 2
            if completed and completed % 100 == 0:
                LOG.info("Compressed %d/%d pending files", completed, len(pending))
    LOG.info("Complete: compressed %d files", completed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
