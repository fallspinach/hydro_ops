"""Acquire one native HRRR terrain record and preserve its grid metadata."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.config import Settings
from hydro_ops.download.hrrr import HrrrDownloader, select_record
from hydro_ops.download.stage4 import is_grib2
from hydro_ops.download.stage4_convert import is_netcdf

TERRAIN_RECORD = ("HGT", "surface", "anl")


def download_hrrr_static(
    settings: Settings, cycle: datetime, output_dir: Path, *, force: bool = False
) -> tuple[Path, Path, Path]:
    """Download HRRR surface height and write NetCDF plus wgrib2 grid description."""
    cycle = cycle.astimezone(UTC) if cycle.tzinfo else cycle.replace(tzinfo=UTC)
    output_dir.mkdir(parents=True, exist_ok=True)
    grib = output_dir / f"hrrr_static.{cycle:%Y%m%d%H}.grib2"
    netcdf = grib.with_suffix(f"{grib.suffix}.nc")
    grid = grib.with_suffix(f"{grib.suffix}.grid.txt")
    if not force and is_grib2(grib) and is_netcdf(netcdf) and grid.is_file():
        return grib, netcdf, grid
    partial = grib.with_name(f"{grib.name}.part")
    partial.unlink(missing_ok=True)
    downloader = HrrrDownloader(settings)
    url = downloader.source_url(cycle, 0)
    try:
        with downloader._session() as session, partial.open("wb") as stream:
            record = select_record(downloader._index(session, url), TERRAIN_RECORD)
            downloader._append_record(session, url, record, stream)
        if not is_grib2(partial):
            raise RuntimeError(f"HRRR terrain subset is not valid GRIB2: {partial}")
        partial.replace(grib)
        downloader.convert(grib, netcdf)
        executable = shutil.which(settings.hrrr_wgrib2)
        if not executable:
            raise RuntimeError(f"HRRR metadata tool not found: {settings.hrrr_wgrib2}")
        description = subprocess.run(
            [executable, str(grib), "-grid"], check=True, capture_output=True, text=True
        ).stdout
        grid.write_text(description)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return grib, netcdf, grid
