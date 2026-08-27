#!/usr/bin/env python3
"""Create an isolated read-only source view with selected forcing products hidden."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

PRODUCT_PATHS = {
    "nldas2": "forcing/nasa/nldas2/fora0125_hourly_v2.0",
    "hrrr": "forcing/noaa/hrrr/conus/3km/hourly",
    "mrms_pass1": "forcing/noaa/mrms/conus/1km/hourly/netcdf/pass1",
    "mrms_pass2": "forcing/noaa/mrms/conus/1km/hourly/netcdf/pass2",
    "mrms_quality": "forcing/noaa/mrms/conus/1km/hourly/netcdf/quality",
    "stage4_archive": "forcing/noaa/stage4/netcdf/archive",
    "stage4_realtime": "forcing/noaa/stage4/netcdf/realtime",
}


def create_view(
    source_data: Path, destination: Path, hidden: set[str], *, force: bool = False
) -> dict:
    source_data = source_data.resolve()
    destination = destination.resolve()
    if destination.exists():
        if not force:
            raise FileExistsError(f"Scenario view exists; use --force to replace it: {destination}")
        shutil.rmtree(destination)
    (destination / "data").mkdir(parents=True)
    links: dict[str, str] = {}

    def link(relative: str) -> None:
        source = source_data / relative
        if not source.exists():
            raise FileNotFoundError(f"Required source path is missing: {source}")
        target = destination / "data" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=source.is_dir())
        links[relative] = str(source)

    link("static")
    for product, relative in PRODUCT_PATHS.items():
        if product not in hidden:
            link(relative)
    manifest = {
        "created": datetime.now(UTC).isoformat(),
        "source_data": str(source_data),
        "scenario_root": str(destination),
        "hidden_products": sorted(hidden),
        "links": links,
    }
    (destination / "scenario.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-data", type=Path, default=Path("data"))
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument(
        "--hide",
        action="append",
        default=[],
        choices=tuple(PRODUCT_PATHS),
        help="product to omit; repeat for multiple products",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            create_view(args.source_data, args.destination, set(args.hide), force=args.force),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
