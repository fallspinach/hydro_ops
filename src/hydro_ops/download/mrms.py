"""Download operational CONUS MRMS hourly precipitation forcing."""

from __future__ import annotations

import concurrent.futures
import gzip
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.config import Settings
from hydro_ops.download.http import apply_remote_mtime, local_matches_remote
from hydro_ops.download.netcdf_compression import convert_grib_with_wgrib2
from hydro_ops.download.stage4 import is_grib2
from hydro_ops.download.stage4_convert import is_netcdf
from hydro_ops.work import temporary_work_root

LOG = logging.getLogger(__name__)

PRODUCTS = {
    "pass1": "MultiSensor_QPE_01H_Pass1_00.00",
    "pass2": "MultiSensor_QPE_01H_Pass2_00.00",
    "quality": "RadarAccumulationQualityIndex_01H_00.00",
}


@dataclass(frozen=True)
class MrmsFile:
    product: str
    valid: datetime
    url: str
    compressed: Path
    netcdf: Path


def is_gzip(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(2) == b"\x1f\x8b"
    except OSError:
        return False


class MrmsDownloader:
    """Keep both Multi-Sensor passes and their radar accumulation quality field."""

    def __init__(self, settings: Settings):
        self.settings = settings
        unknown = set(settings.mrms_products) - PRODUCTS.keys()
        if unknown:
            raise ValueError(f"Unknown MRMS products: {', '.join(sorted(unknown))}")

    @property
    def timeout(self) -> tuple[int, int]:
        return self.settings.mrms_connect_timeout, self.settings.mrms_read_timeout

    def _session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.settings.mrms_retries,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def file(self, product: str, valid: datetime) -> MrmsFile:
        if product not in PRODUCTS:
            raise ValueError(f"Unknown MRMS product: {product}")
        valid = valid.astimezone(UTC) if valid.tzinfo else valid.replace(tzinfo=UTC)
        remote_product = PRODUCTS[product]
        name = f"MRMS_{remote_product}_{valid:%Y%m%d-%H0000}.grib2.gz"
        url = (
            f"{self.settings.mrms_base_url}/CONUS/{remote_product}/{valid:%Y%m%d}/{name}"
        )
        relative = Path(product) / valid.strftime("%Y/%m/%d")
        compressed = self.settings.mrms_data_dir / "raw" / relative / name
        netcdf = self.settings.mrms_data_dir / "netcdf" / relative / f"{name[:-3]}.nc"
        return MrmsFile(product, valid, url, compressed, netcdf)

    def download_one(self, item: MrmsFile, *, dry_run: bool = False) -> str:
        if dry_run:
            LOG.info("Would download %s", item.url)
            return "dry-run"
        with self._session() as session:
            response = session.head(item.url, timeout=self.timeout)
            response.raise_for_status()
            current = is_gzip(item.compressed) and local_matches_remote(
                item.compressed, response.headers
            )
            if current and is_netcdf(item.netcdf):
                LOG.info("SKIP current %s", item.netcdf)
                return "skipped"
            if not current:
                self._download(session, item, response.headers)
        self._convert(item)
        return "converted" if current else "downloaded"

    def _download(self, session: requests.Session, item: MrmsFile, headers) -> None:
        item.compressed.parent.mkdir(parents=True, exist_ok=True)
        partial = item.compressed.with_name(f"{item.compressed.name}.part")
        partial.unlink(missing_ok=True)
        LOG.info("GET  %s", item.url)
        try:
            with session.get(item.url, stream=True, timeout=self.timeout) as response:
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            if not is_gzip(partial):
                raise RuntimeError(f"Downloaded response is not gzip: {item.url}")
            partial.replace(item.compressed)
            apply_remote_mtime(item.compressed, headers)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _convert(self, item: MrmsFile) -> None:
        executable = shutil.which(self.settings.mrms_wgrib2)
        if not executable:
            raise RuntimeError(
                f"MRMS conversion tool not found: {self.settings.mrms_wgrib2}; "
                "install/update the hydro-ops environment"
            )
        item.netcdf.parent.mkdir(parents=True, exist_ok=True)
        work_root = temporary_work_root(self.settings, "mrms-conversion")
        grib = work_root / f"{item.netcdf.stem}.part.grib2"
        partial = item.netcdf.with_name(f"{item.netcdf.name}.part")
        grib.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        LOG.info("CONVERT %s -> %s", item.compressed, item.netcdf)
        try:
            with gzip.open(item.compressed, "rb") as source, grib.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            if not is_grib2(grib):
                raise RuntimeError(f"MRMS gzip does not contain GRIB2: {item.compressed}")
            convert_grib_with_wgrib2(
                executable, grib, partial, level=4, work_directory=work_root
            )
            if not is_netcdf(partial):
                raise RuntimeError(f"wgrib2 did not create valid NetCDF: {item.compressed}")
            partial.replace(item.netcdf)
            modified = item.compressed.stat().st_mtime
            os.utime(item.netcdf, (modified, modified))
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            grib.unlink(missing_ok=True)

    def best_available(self, valid: datetime) -> Path | None:
        """Return Pass 2 when present, otherwise Pass 1, for one valid hour."""
        for product in ("pass2", "pass1"):
            candidate = self.file(product, valid).netcdf
            if is_netcdf(candidate):
                return candidate
        return None

    def download_day(
        self,
        day: date,
        *,
        latest: datetime | None = None,
        allow_missing: bool = False,
        dry_run: bool = False,
    ) -> tuple[int, int]:
        hours = [datetime(day.year, day.month, day.day, hour, tzinfo=UTC) for hour in range(24)]
        if latest is not None:
            latest = latest.astimezone(UTC) if latest.tzinfo else latest.replace(tzinfo=UTC)
            hours = [hour for hour in hours if hour <= latest]
        items = [self.file(product, hour) for hour in hours for product in self.settings.mrms_products]
        changed = 0
        if dry_run:
            for item in items:
                self.download_one(item, dry_run=True)
        else:
            def fetch(item: MrmsFile) -> str:
                try:
                    return self.download_one(item)
                except requests.HTTPError as error:
                    if allow_missing and error.response is not None and error.response.status_code == 404:
                        LOG.info("WAIT unavailable %s", item.url)
                        return "unavailable"
                    raise

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.settings.mrms_download_jobs
            ) as executor:
                for result in executor.map(fetch, items):
                    changed += result in {"downloaded", "converted"}
        LOG.info("Complete: MRMS %s (%d products, %d updated)", day, len(items), changed)
        return len(items), changed
