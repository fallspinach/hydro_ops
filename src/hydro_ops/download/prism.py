"""Revision-aware PRISM AN 4-km daily forcing downloader."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.config import Settings
from hydro_ops.download.stage4_convert import is_netcdf
from hydro_ops.work import temporary_work_root

LOG = logging.getLogger(__name__)
NORMALIZATION_VERSION = 1
VARIABLES = {
    "ppt": ("precipitation_amount", "daily total precipitation", "mm", "time: sum"),
    "tmean": ("air_temperature", "daily mean air temperature", "degC", "time: mean"),
    "tmax": ("air_temperature", "daily maximum air temperature", "degC", "time: maximum"),
    "tmin": ("air_temperature", "daily minimum air temperature", "degC", "time: minimum"),
}
MONTHLY_CELL_METHODS = {
    "ppt": "time: sum",
    "tmean": "time: mean",
    "tmax": "time: maximum within days time: mean over days",
    "tmin": "time: minimum within days time: mean over days",
}
MONTHLY_LONG_NAMES = {
    "ppt": "monthly total precipitation",
    "tmean": "monthly mean air temperature",
    "tmax": "monthly mean of daily maximum air temperature",
    "tmin": "monthly mean of daily minimum air temperature",
}


def normalize_netcdf(source: Path, destination: Path, day: date, element: str = "ppt") -> None:
    """Give PRISM's generic raster variable forcing-ready CF names and time metadata."""
    with xr.open_dataset(source) as raw:
        if "Band1" not in raw:
            raise RuntimeError(f"Expected Band1 in PRISM NetCDF: {source}")
        standard_name, long_name, units, cell_methods = VARIABLES[element]
        variable = (
            raw["Band1"]
            .rename(element)
            .expand_dims(time=[np.datetime64(f"{day.isoformat()}T12:00:00")])
        )
        variable.attrs.update(
            standard_name=standard_name,
            long_name=long_name,
            units=units,
            cell_methods=cell_methods,
            grid_mapping="crs",
        )
        normalized = xr.Dataset(
            data_vars={element: variable, "crs": raw["crs"]},
            attrs={
                **raw.attrs,
                "title": f"PRISM AN 4-km {long_name}",
                "source": "PRISM Climate Group, Oregon State University",
                "time_coverage_end": f"{day.isoformat()}T12:00:00Z",
                "time_coverage_start": f"{(day - timedelta(days=1)).isoformat()}T12:00:00Z",
            },
        )
        normalized.time.attrs.update(standard_name="time", axis="T")
        encoding = {
            element: {
                "dtype": "float32",
                "zlib": True,
                "complevel": 4,
                "shuffle": True,
                "_FillValue": np.float32(-9999.0),
            }
        }
        normalized.to_netcdf(destination, engine="netcdf4", encoding=encoding)


def normalize_monthly_netcdf(
    source: Path, destination: Path, year: int, month: int, element: str
) -> None:
    """Normalize one historical PRISM monthly grid without inventing daily values."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    with xr.open_dataset(source) as raw:
        if "Band1" not in raw:
            raise RuntimeError(f"Expected Band1 in PRISM NetCDF: {source}")
        standard_name, _, units, _ = VARIABLES[element]
        description = MONTHLY_LONG_NAMES[element]
        variable = raw["Band1"].rename(element).expand_dims(
            time=[np.datetime64(start.isoformat())]
        )
        variable.attrs.update(
            standard_name=standard_name,
            long_name=description,
            units=units,
            cell_methods=MONTHLY_CELL_METHODS[element],
            grid_mapping="crs",
        )
        normalized = xr.Dataset(
            data_vars={element: variable, "crs": raw["crs"]},
            attrs={
                **raw.attrs,
                "title": f"PRISM AN 4-km {description}",
                "source": "PRISM Climate Group, Oregon State University",
                "temporal_resolution": "monthly",
                "time_coverage_start": f"{start.isoformat()}T00:00:00Z",
                "time_coverage_end": f"{end.isoformat()}T00:00:00Z",
            },
        )
        normalized.time.attrs.update(standard_name="time", axis="T")
        normalized.to_netcdf(
            destination,
            engine="netcdf4",
            encoding={
                element: {
                    "dtype": "float32",
                    "zlib": True,
                    "complevel": 4,
                    "shuffle": True,
                    "_FillValue": np.float32(-9999.0),
                }
            },
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_monthly_year(
    settings: Settings,
    year: int,
    elements: tuple[str, ...],
    *,
    force: bool = False,
) -> list[Path]:
    """Download and normalize stable historical PRISM monthly grids for one year."""
    unknown = set(elements) - set(VARIABLES)
    if unknown:
        raise ValueError(f"Unknown PRISM elements: {sorted(unknown)}")
    daily_root = settings.prism_data_dir
    monthly_root = daily_root.parent / "monthly"
    work_root = temporary_work_root(settings, "prism-monthly")
    work_root.mkdir(parents=True, exist_ok=True)
    downloader = PrismDownloader(settings)
    published: list[Path] = []
    for element in elements:
        for month in range(1, 13):
            name = f"prism_{element}_us_25m_{year:04d}{month:02d}.nc"
            destination = monthly_root / element / f"{year:04d}" / name
            if not force and is_netcdf(destination):
                published.append(destination)
                continue
            url = (
                f"{settings.prism_base_url}/us/4km/{element}/"
                f"{year:04d}{month:02d}?format=nc"
            )
            temporary = tempfile.TemporaryDirectory(dir=work_root)
            temporary_path = Path(temporary.name)
            archive = temporary_path / f"prism_{element}_{year:04d}{month:02d}.zip"
            with (
                downloader._session() as session,
                session.get(url, stream=True, timeout=downloader.timeout) as response,
            ):
                response.raise_for_status()
                with archive.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            if not zipfile.is_zipfile(archive):
                raise RuntimeError(f"Downloaded PRISM response is not a ZIP archive: {url}")
            archive_sha256 = _sha256(archive)
            with zipfile.ZipFile(archive) as bundle:
                by_name = {Path(name).name: name for name in bundle.namelist()}
                member = by_name.get(name)
                if member is None:
                    raise RuntimeError(f"Historical PRISM archive is missing {name}: {url}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                extracted = temporary_path / name
                with bundle.open(member) as source, extracted.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
                normalized = temporary_path / f"normalized-{name}"
                normalize_monthly_netcdf(extracted, normalized, year, month, element)
                if not is_netcdf(normalized):
                    raise RuntimeError(f"Could not normalize PRISM NetCDF: {name}")
                partial = destination.with_name(f"{destination.name}.part")
                shutil.copyfile(normalized, partial)
                partial.replace(destination)
            metadata = destination.with_suffix(".release.json")
            metadata_partial = metadata.with_suffix(".json.part")
            metadata_partial.write_text(
                json.dumps(
                    {
                        "archive_sha256": archive_sha256,
                        "data_month": f"{year:04d}-{month:02d}",
                        "downloaded_at": datetime.now(UTC).isoformat(),
                        "element": element,
                        "format": "netcdf",
                        "normalization_version": NORMALIZATION_VERSION,
                        "source_url": url,
                        "stability": "historical",
                        "temporal_resolution": "monthly",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            metadata_partial.replace(metadata)
            published.append(destination)
            temporary.cleanup()
            if settings.prism_request_delay:
                time.sleep(settings.prism_request_delay)
    return published


@dataclass(frozen=True)
class PrismRelease:
    data_date: date
    release_date: date
    grid_count: int
    data_url: str
    element: str

    @classmethod
    def from_row(cls, row: list[str]) -> PrismRelease:
        if len(row) != 5 or row[2] not in VARIABLES:
            raise RuntimeError(f"Unexpected PRISM release metadata: {row!r}")
        return cls(
            date.fromisoformat(row[0]), date.fromisoformat(row[1]), int(row[3]), row[4], row[2]
        )


class PrismDownloader:
    """Download only changed PRISM daily precipitation grids as NetCDF."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def timeout(self) -> tuple[int, int]:
        return self.settings.prism_connect_timeout, self.settings.prism_read_timeout

    def _session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.settings.prism_retries,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def releases(self, start: date, end: date, element: str = "ppt") -> list[PrismRelease]:
        if start > end:
            raise ValueError("Start date is after end date")
        candidate_end = end
        for _ in range(15):
            url = (
                f"{self.settings.prism_base_url}/releaseDate/us/4km/{element}/"
                f"{start:%Y%m%d}/{candidate_end:%Y%m%d}?json=true"
            )
            with self._session() as session:
                response = session.get(url, timeout=self.timeout)
                response.raise_for_status()
            if response.text.startswith("Invalid date:") and candidate_end > start:
                candidate_end -= timedelta(days=1)
                continue
            try:
                rows = response.json()
                releases = [PrismRelease.from_row(row) for row in rows if row[1] and row[3]]
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(f"Invalid PRISM release metadata from {url}") from error
            if candidate_end != end:
                LOG.info("PRISM is published through %s; requested end was %s", candidate_end, end)
            return releases
        raise RuntimeError(f"Could not find published PRISM data near requested end date {end}")

    def paths(self, day: date, element: str = "ppt") -> tuple[Path, Path]:
        name = f"prism_{element}_us_25m_{day:%Y%m%d}.nc"
        destination = self.settings.prism_data_dir / element / day.strftime("%Y/%m") / name
        return destination, destination.with_suffix(".release.json")

    @staticmethod
    def _metadata_matches(path: Path, release: PrismRelease) -> bool:
        try:
            values = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return (
            values.get("release_date") == release.release_date.isoformat()
            and values.get("grid_count") == release.grid_count
            and values.get("normalization_version") == NORMALIZATION_VERSION
        )

    def is_current(self, release: PrismRelease) -> bool:
        destination, metadata = self.paths(release.data_date, release.element)
        return is_netcdf(destination) and self._metadata_matches(metadata, release)

    def download_one(self, release: PrismRelease) -> str:
        destination, metadata = self.paths(release.data_date, release.element)
        if self.is_current(release):
            LOG.info("SKIP current %s (grid count %d)", destination, release.grid_count)
            return "skipped"
        destination.parent.mkdir(parents=True, exist_ok=True)
        work_root = temporary_work_root(self.settings, "prism")
        url = f"{release.data_url}?format=nc"
        LOG.info("GET  %s (grid count %d)", url, release.grid_count)
        with tempfile.TemporaryDirectory(dir=work_root) as temporary:
            temporary_path = Path(temporary)
            archive = temporary_path / f"prism_{release.data_date:%Y%m%d}.zip"
            with (
                self._session() as session,
                session.get(url, stream=True, timeout=self.timeout) as response,
            ):
                response.raise_for_status()
                with archive.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            if not zipfile.is_zipfile(archive):
                raise RuntimeError(f"Downloaded PRISM response is not a ZIP archive: {url}")
            expected = f"prism_{release.element}_us_25m_{release.data_date:%Y%m%d}.nc"
            with zipfile.ZipFile(archive) as bundle:
                names = [name for name in bundle.namelist() if Path(name).name == expected]
                if len(names) != 1:
                    raise RuntimeError(f"Expected one {expected} in {archive}, found {len(names)}")
                extracted = temporary_path / expected
                with bundle.open(names[0]) as source, extracted.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
            if not is_netcdf(extracted):
                raise RuntimeError(f"PRISM archive contains invalid NetCDF: {expected}")
            normalized = temporary_path / f"normalized-{expected}"
            normalize_netcdf(extracted, normalized, release.data_date, release.element)
            if not is_netcdf(normalized):
                raise RuntimeError(f"Could not normalize PRISM NetCDF: {expected}")
            partial = destination.with_name(f"{destination.name}.part")
            shutil.copyfile(normalized, partial)
            partial.replace(destination)
        record = {
            **asdict(release),
            "data_date": release.data_date.isoformat(),
            "release_date": release.release_date.isoformat(),
            "downloaded_at": datetime.now(UTC).isoformat(),
            "format": "netcdf",
            "normalization_version": NORMALIZATION_VERSION,
        }
        metadata_partial = metadata.with_name(f"{metadata.name}.part")
        metadata_partial.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        metadata_partial.replace(metadata)
        return "downloaded"

    def download_range(
        self, start: date, end: date, elements: tuple[str, ...], *, dry_run: bool = False
    ) -> tuple[int, int]:
        releases = [
            release for element in elements for release in self.releases(start, end, element)
        ]
        downloaded = 0
        for release in releases:
            if dry_run:
                state = "current" if self.is_current(release) else "would download"
                LOG.info("%s %s (grid count %d)", state, release.data_date, release.grid_count)
                continue
            result = self.download_one(release)
            downloaded += result == "downloaded"
            if result == "downloaded" and self.settings.prism_request_delay:
                time.sleep(self.settings.prism_request_delay)
        LOG.info(
            "Complete: PRISM %s through %s (%d releases, %d downloaded)",
            start,
            end,
            len(releases),
            downloaded,
        )
        return len(releases), downloaded
