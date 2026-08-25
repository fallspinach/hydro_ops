"""Convert nested monthly Stage-IV GRIB1 archives to the canonical NetCDF grid."""

from __future__ import annotations

import gzip
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.config import Settings
from hydro_ops.forcing.daily_archive import create_daily_archive, verified_daily_archive
from hydro_ops.work import temporary_work_root

LEGACY_HOURLY = re.compile(r"^ST4\.(\d{10})\.01h\.(Z|gz)$")
FILL_VALUE = np.float32(9.999e20)
TIME_UNITS = "seconds since 1970-01-01 00:00:00.0 0:00"


def _attributes(variable) -> dict:
    return {name: variable.getncattr(name) for name in variable.ncattrs() if name != "_FillValue"}


def _decompress(name: str, payload: bytes, work: Path) -> bytes:
    if name.endswith(".gz"):
        return gzip.decompress(payload)
    compressed = work / Path(name).name
    compressed.write_bytes(payload)
    try:
        return subprocess.run(
            ["uncompress", "-c", str(compressed)], check=True, capture_output=True
        ).stdout
    finally:
        compressed.unlink(missing_ok=True)


def _decode_wgrib(wgrib: str, payload: bytes, work: Path) -> np.ndarray:
    source = work / "legacy.grb"
    binary = work / "legacy.bin"
    source.write_bytes(payload)
    inventory = subprocess.run(
        [wgrib, str(source), "-s"], check=True, capture_output=True, text=True
    ).stdout
    if "APCP" not in inventory or "0-1hr acc" not in inventory:
        raise RuntimeError(f"Unexpected legacy Stage-IV field: {inventory.strip()}")
    subprocess.run(
        [wgrib, str(source), "-d", "1", "-bin", "-nh", "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    values = np.fromfile(binary, dtype=np.float32)
    source.unlink(missing_ok=True)
    binary.unlink(missing_ok=True)
    return values


def write_legacy_hourly_netcdf(
    destination: Path,
    template: Path,
    values: np.ndarray,
    valid_time: datetime,
    source_name: str,
) -> Path:
    """Write one decoded GRIB1 field using the canonical Stage-IV grid schema."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    partial.unlink(missing_ok=True)
    with Dataset(template) as grid, Dataset(partial, "w", format="NETCDF4") as output:
        ny, nx = grid["latitude"].shape
        if values.size != ny * nx:
            raise ValueError(
                f"Legacy grid has {values.size} cells; canonical grid has {ny * nx}"
            )
        output.createDimension("y", ny)
        output.createDimension("x", nx)
        output.createDimension("time", None)
        for name in ("y", "x", "latitude", "longitude"):
            source = grid[name]
            variable = output.createVariable(name, source.dtype, source.dimensions)
            variable.setncatts(_attributes(source))
            variable[:] = source[:]
        time = output.createVariable("time", "f8", ("time",))
        time.setncatts(
            {
                "units": TIME_UNITS,
                "long_name": "verification time decoded from legacy Stage-IV filename",
                "reference_time": (valid_time - timedelta(hours=1)).timestamp(),
                "reference_time_type": 3,
                "reference_date": (valid_time - timedelta(hours=1)).strftime(
                    "%Y.%m.%d %H:%M:%S UTC"
                ),
                "reference_time_description": "start of one-hour accumulation",
                "time_step_setting": "auto",
                "time_step": 0.0,
            }
        )
        time[0] = valid_time.timestamp()
        precipitation = output.createVariable(
            "APCP_surface", "f4", ("time", "y", "x"), fill_value=FILL_VALUE
        )
        precipitation.setncatts(
            {
                "short_name": "APCP_surface",
                "long_name": "Total Precipitation",
                "level": "surface",
                "units": "kg/m^2",
                "coordinates": "longitude latitude",
            }
        )
        field = values.reshape(ny, nx)
        precipitation[0] = np.ma.masked_where(field >= 9.0e20, field)
        output.setncatts(
            {
                "Conventions": "CF-1.0",
                "History": "decoded from NCEP Stage-IV GRIB1 by hydro_ops",
                "source_archive_member": source_name,
                "source_grib_edition": 1,
                "canonical_grid_template": str(template),
            }
        )
    partial.replace(destination)
    return destination


class LegacyStage4Converter:
    def __init__(self, settings: Settings, template: Path):
        self.settings = settings
        self.template = template
        executable = shutil.which("wgrib")
        if not executable:
            raise RuntimeError("Legacy Stage-IV conversion requires wgrib")
        if not template.is_file():
            raise FileNotFoundError(f"Stage-IV grid template not found: {template}")
        self.wgrib = executable

    def convert_month(self, archive: Path, *, delete_hourly: bool = True) -> tuple[int, int]:
        """Convert all complete days in one nested monthly archive."""
        converted_days = skipped_days = 0
        work_root = temporary_work_root(self.settings, "stage4-legacy")
        with tarfile.open(archive) as monthly:
            for daily_member in monthly.getmembers():
                if not daily_member.isfile() or not re.fullmatch(r"ST4\.\d{8}", daily_member.name):
                    continue
                day_stamp = daily_member.name.removeprefix("ST4.")
                day = date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))
                destination = (
                    self.settings.stage4_data_dir / "netcdf/archive" / day.strftime("%Y/%m")
                    / f"stage4_archive_01h.{day_stamp}.nc"
                )
                if verified_daily_archive(destination, day):
                    skipped_days += 1
                    continue
                extracted = monthly.extractfile(daily_member)
                if extracted is None:
                    raise RuntimeError(f"Could not read {daily_member.name} from {archive}")
                payload = extracted.read()
                hourly_paths: list[Path] = []
                with (
                    tarfile.open(fileobj=io.BytesIO(payload)) as daily,
                    tempfile.TemporaryDirectory(dir=work_root) as temporary,
                ):
                    temporary_path = Path(temporary)
                    for member in daily.getmembers():
                        name = Path(member.name).name
                        match = LEGACY_HOURLY.fullmatch(name)
                        if member.name != name or not member.isfile() or not match:
                            continue
                        source = daily.extractfile(member)
                        if source is None:
                            raise RuntimeError(f"Could not read {member.name} from {daily_member.name}")
                        valid_time = datetime.strptime(match.group(1), "%Y%m%d%H").replace(
                            tzinfo=UTC
                        )
                        values = _decode_wgrib(
                            self.wgrib, _decompress(name, source.read(), temporary_path), temporary_path
                        )
                        hourly = (
                            self.settings.stage4_data_dir / "netcdf/archive"
                            / valid_time.strftime("%Y/%m/%d")
                            / f"st4_conus.{valid_time:%Y%m%d%H}.01h.grib1.nc"
                        )
                        hourly_paths.append(
                            write_legacy_hourly_netcdf(
                                hourly, self.template, values, valid_time, name
                            )
                        )
                hourly_paths.sort()
                create_daily_archive(
                    hourly_paths,
                    destination,
                    day,
                    compression_level=2,
                    work_directory=work_root,
                )
                if delete_hourly:
                    for path in hourly_paths:
                        path.unlink()
                    hourly_paths[0].parent.rmdir()
                converted_days += 1
        return converted_days, skipped_days
