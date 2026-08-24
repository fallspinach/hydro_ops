"""Subset hourly HRRR hydrologic forcing from NOAA's public AWS archive."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.config import Settings
from hydro_ops.download.netcdf_compression import convert_grib_with_wgrib2
from hydro_ops.download.stage4 import is_grib2
from hydro_ops.download.stage4_convert import is_netcdf
from hydro_ops.work import temporary_work_root

LOG = logging.getLogger(__name__)

# The first seven records are analyses. Hourly precipitation is the preceding
# cycle's f01 accumulation because f00 APCP has a zero-length accumulation window.
ANALYSIS_RECORDS = (
    ("TMP", "2 m above ground", "anl"),
    ("SPFH", "2 m above ground", "anl"),
    ("PRES", "surface", "anl"),
    ("DSWRF", "surface", "anl"),
    ("DLWRF", "surface", "anl"),
    ("UGRD", "10 m above ground", "anl"),
    ("VGRD", "10 m above ground", "anl"),
)
PRECIPITATION_RECORD = ("APCP", "surface", "0-1 hour acc fcst")


@dataclass(frozen=True)
class IndexRecord:
    number: int
    offset: int
    variable: str
    level: str
    timing: str
    end: int | None = None


def parse_index(text: str) -> list[IndexRecord]:
    """Parse the relevant fields from an HRRR wgrib2-style index."""
    records: list[IndexRecord] = []
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) < 6:
            continue
        try:
            number, offset = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        records.append(IndexRecord(number, offset, fields[3], fields[4], fields[5]))
    return [
        IndexRecord(
            record.number,
            record.offset,
            record.variable,
            record.level,
            record.timing,
            records[index + 1].offset - 1 if index + 1 < len(records) else None,
        )
        for index, record in enumerate(records)
    ]


def select_record(records: list[IndexRecord], selector: tuple[str, str, str]) -> IndexRecord:
    matches = [
        record
        for record in records
        if (record.variable, record.level, record.timing) == selector
    ]
    if len(matches) != 1:
        label = ":".join(selector)
        raise RuntimeError(f"Expected exactly one HRRR index record for {label}; found {len(matches)}")
    if matches[0].end is None:
        raise RuntimeError(f"Cannot determine byte range for final HRRR index record: {label}")
    return matches[0]


class HrrrDownloader:
    """Download only the HRRR records needed for hydrologic forcing."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def timeout(self) -> tuple[int, int]:
        return self.settings.hrrr_connect_timeout, self.settings.hrrr_read_timeout

    def _session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.settings.hrrr_retries,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def source_url(self, cycle: datetime, forecast_hour: int) -> str:
        name = f"hrrr.t{cycle:%H}z.wrfsfcf{forecast_hour:02d}.grib2"
        return f"{self.settings.hrrr_base_url}/hrrr.{cycle:%Y%m%d}/conus/{name}"

    def destination(self, valid: datetime, suffix: str) -> Path:
        name = f"hrrr_forcing.{valid:%Y%m%d%H}.grib2{suffix}"
        return self.settings.hrrr_data_dir / valid.strftime("%Y/%m/%d") / name

    def _index(self, session: requests.Session, url: str) -> list[IndexRecord]:
        response = session.get(f"{url}.idx", timeout=self.timeout)
        response.raise_for_status()
        records = parse_index(response.text)
        if not records:
            raise RuntimeError(f"Invalid or empty HRRR index: {url}.idx")
        return records

    def _append_record(
        self,
        session: requests.Session,
        url: str,
        record: IndexRecord,
        stream,
    ) -> None:
        assert record.end is not None
        response = session.get(
            url,
            headers={"Range": f"bytes={record.offset}-{record.end}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.status_code != 206:
            raise RuntimeError(f"HRRR server ignored byte-range request for {url}")
        expected = record.end - record.offset + 1
        if len(response.content) != expected or not response.content.startswith(b"GRIB"):
            raise RuntimeError(f"Invalid HRRR GRIB2 record returned by {url}")
        stream.write(response.content)

    def download_hour(self, valid: datetime, *, dry_run: bool = False) -> str:
        valid = valid.astimezone(UTC) if valid.tzinfo else valid.replace(tzinfo=UTC)
        analysis_url = self.source_url(valid, 0)
        precip_url = self.source_url(valid - timedelta(hours=1), 1)
        destination = self.destination(valid, "")
        netcdf = self.destination(valid, ".nc")
        if dry_run:
            LOG.info(
                "Would subset 7 analysis records from %s and hourly APCP from %s",
                analysis_url,
                precip_url,
            )
            return "dry-run"
        if is_grib2(destination) and is_netcdf(netcdf):
            LOG.info("SKIP current %s", netcdf)
            return "skipped"

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        LOG.info("GET  HRRR forcing valid %s", valid.strftime("%Y-%m-%d %H:00 UTC"))
        try:
            with self._session() as session, partial.open("wb") as stream:
                analysis_index = self._index(session, analysis_url)
                for selector in ANALYSIS_RECORDS:
                    self._append_record(
                        session, analysis_url, select_record(analysis_index, selector), stream
                    )
                precip_index = self._index(session, precip_url)
                self._append_record(
                    session,
                    precip_url,
                    select_record(precip_index, PRECIPITATION_RECORD),
                    stream,
                )
            if not is_grib2(partial):
                raise RuntimeError(f"HRRR subset is not valid GRIB2: {partial}")
            partial.replace(destination)
            self.convert(destination, netcdf)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return "downloaded"

    def convert(self, source: Path, destination: Path) -> None:
        executable = shutil.which(self.settings.hrrr_wgrib2)
        if not executable:
            raise RuntimeError(
                f"HRRR conversion tool not found: {self.settings.hrrr_wgrib2}; "
                "install/update the hydro-ops environment"
            )
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        LOG.info("CONVERT %s -> %s", source, destination)
        try:
            convert_grib_with_wgrib2(
                executable,
                source,
                partial,
                work_directory=temporary_work_root(self.settings, "hrrr-conversion"),
            )
            if not is_netcdf(partial):
                raise RuntimeError(f"wgrib2 did not create valid NetCDF: {source}")
            partial.replace(destination)
            modified = source.stat().st_mtime
            os.utime(destination, (modified, modified))
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def download_day(self, day: date, *, dry_run: bool = False) -> tuple[int, int]:
        hours = [datetime(day.year, day.month, day.day, hour, tzinfo=UTC) for hour in range(24)]
        downloaded = 0
        if dry_run:
            for hour in hours:
                self.download_hour(hour, dry_run=True)
        else:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.settings.hrrr_download_jobs
            ) as executor:
                for result in executor.map(self.download_hour, hours):
                    downloaded += result == "downloaded"
        LOG.info("Complete: HRRR %s (24 hours, %d downloaded)", day, downloaded)
        return len(hours), downloaded
