"""Convert native-grid CONUS Stage-IV GRIB2 products to NetCDF."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from hydro_ops.config import Settings
from hydro_ops.work import temporary_work_root

LOG = logging.getLogger(__name__)
CONUS_GRIB2 = re.compile(r"^st4_conus\.[0-9]{10}\.(?:01h|06h|24h)\.grb2$")
NETCDF_MAGICS = (b"CDF\x01", b"CDF\x02", b"\x89HDF\r\n\x1a\n")


def is_netcdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            magic = stream.read(8)
    except OSError:
        return False
    return any(magic.startswith(signature) for signature in NETCDF_MAGICS)


class Stage4Converter:
    """Preserve each source field and mask in a separate NetCDF file."""

    def __init__(self, settings: Settings):
        self.settings = settings
        executable = shutil.which(settings.stage4_wgrib2)
        if not executable:
            raise RuntimeError(
                f"Stage-IV conversion tool not found: {settings.stage4_wgrib2}; "
                "install/update the hydro-ops environment"
            )
        self.wgrib2 = executable

    def destination(self, source_name: str, stream: str) -> Path:
        stamp = source_name.removeprefix("st4_conus.")[:10]
        return (
            self.settings.stage4_data_dir
            / "netcdf"
            / stream
            / stamp[:4]
            / stamp[4:6]
            / stamp[6:8]
            / f"{source_name}.nc"
        )

    def convert_grib2(
        self, source: Path, stream: str, *, refresh: bool = False, source_name: str | None = None
    ) -> str:
        name = source_name or source.name
        if not CONUS_GRIB2.fullmatch(name):
            return "ignored"
        destination = self.destination(name, stream)
        if (
            not refresh
            and is_netcdf(destination)
            and destination.stat().st_mtime >= source.stat().st_mtime
        ):
            LOG.info("SKIP current %s", destination)
            return "skipped"
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        LOG.info("CONVERT %s -> %s", source, destination)
        try:
            subprocess.run(
                [self.wgrib2, str(source), "-netcdf", str(partial)],
                check=True,
                capture_output=True,
                text=True,
            )
            if not is_netcdf(partial):
                raise RuntimeError(f"wgrib2 did not create valid NetCDF: {source}")
            partial.replace(destination)
            modified = source.stat().st_mtime
            os.utime(destination, (modified, modified))
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return "converted"

    def convert_archive(self, archive: Path) -> tuple[int, int]:
        selected = converted = 0
        work_root = temporary_work_root(self.settings, "stage4")
        with (
            tempfile.TemporaryDirectory(dir=work_root) as temporary,
            tarfile.open(archive) as bundle,
        ):
            temporary_path = Path(temporary)
            for member in bundle.getmembers():
                name = Path(member.name).name
                if member.name != name or not member.isfile() or not CONUS_GRIB2.fullmatch(name):
                    continue
                selected += 1
                source = temporary_path / name
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"Could not read {member.name} from {archive}")
                with extracted, source.open("wb") as stream:
                    shutil.copyfileobj(extracted, stream)
                os.utime(source, (member.mtime, member.mtime))
                converted += self.convert_grib2(source, "archive", source_name=name) == "converted"
        if not selected:
            raise RuntimeError(f"No CONUS Stage-IV GRIB2 members found in {archive}")
        LOG.info("Converted archive %s (%d selected, %d converted)", archive, selected, converted)
        return selected, converted

    def convert_existing(self, stream: str) -> tuple[int, int]:
        """Convert every locally available raw file without contacting NOAA."""
        selected = converted = 0
        if stream == "archive":
            sources = sorted((self.settings.stage4_data_dir / "archive").glob("*/*/ST4.*.tar"))
            for source in sources:
                found, made = self.convert_archive(source)
                selected += found
                converted += made
        elif stream == "realtime":
            sources = sorted(
                (self.settings.stage4_data_dir / "realtime").glob("*/*/*/st4_conus.*.grb2")
            )
            for source in sources:
                if CONUS_GRIB2.fullmatch(source.name):
                    selected += 1
                    converted += self.convert_grib2(source, stream) == "converted"
        else:
            raise ValueError(f"Unknown Stage-IV stream: {stream}")
        LOG.info(
            "Complete: local Stage-IV %s conversion (%d selected, %d converted)",
            stream,
            selected,
            converted,
        )
        return selected, converted
