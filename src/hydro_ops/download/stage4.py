"""NOAA Stage-IV realtime and stable archive downloaders."""

from __future__ import annotations

import concurrent.futures
import logging
import re
import tarfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.config import Settings
from hydro_ops.download.http import apply_remote_mtime, local_matches_remote
from hydro_ops.download.stage4_convert import Stage4Converter

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage4File:
    url: str
    destination: Path
    kind: str


def is_grib2(path: Path) -> bool:
    try:
        if path.stat().st_size < 12:
            return False
        with path.open("rb") as stream:
            return stream.read(4) == b"GRIB"
    except OSError:
        return False


def is_tar(path: Path) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except OSError:
        return False


class Stage4Downloader:
    """Download Stage-IV files from either NOAA distribution stream."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def timeout(self) -> tuple[int, int]:
        return self.settings.stage4_connect_timeout, self.settings.stage4_read_timeout

    def _session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.settings.stage4_retries,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def discover_realtime(self, day: date) -> list[Stage4File]:
        stamp = day.strftime("%Y%m%d")
        remote_dir = f"{self.settings.stage4_realtime_base_url}/pcpanl.{stamp}/"
        pattern = re.compile(rf'href=["\'](st4_conus\.{stamp}[0-9]{{2}}\.[0-9]{{2}}h\.grb2)["\']')
        with self._session() as session:
            response = session.get(remote_dir, timeout=self.timeout)
            response.raise_for_status()
        names = sorted(set(pattern.findall(response.text)))
        if not names:
            raise RuntimeError(f"No Stage-IV GRIB2 files found for {day} at {remote_dir}")
        local_dir = self.settings.stage4_data_dir / "realtime" / day.strftime("%Y/%m/%d")
        return [Stage4File(urljoin(remote_dir, name), local_dir / name, "grib2") for name in names]

    def archive_file(self, day: date) -> Stage4File:
        stamp = day.strftime("%Y%m%d")
        name = f"ST4.{stamp}.tar"
        url = f"{self.settings.stage4_archive_base_url}/{day:%Y%m}/{name}"
        destination = self.settings.stage4_data_dir / "archive" / day.strftime("%Y/%m") / name
        return Stage4File(url, destination, "tar")

    def download_one(self, item: Stage4File, *, refresh: bool = False) -> str:
        valid = is_grib2 if item.kind == "grib2" else is_tar
        if not refresh and valid(item.destination):
            with self._session() as session:
                response = session.head(item.url, timeout=self.timeout)
                response.raise_for_status()
            if local_matches_remote(item.destination, response.headers):
                LOG.info("SKIP current %s", item.destination)
                return "skipped"
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        partial = item.destination.with_name(f"{item.destination.name}.part")
        partial.unlink(missing_ok=True)
        LOG.info("GET  %s", item.url)
        try:
            with (
                self._session() as session,
                session.get(item.url, stream=True, timeout=self.timeout) as response,
            ):
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            if not valid(partial):
                raise RuntimeError(f"Downloaded response is not valid {item.kind}: {item.url}")
            partial.replace(item.destination)
            apply_remote_mtime(item.destination, response.headers)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return "downloaded"

    def download_day(self, day: date, stream: str, *, dry_run: bool = False) -> tuple[int, int]:
        if stream == "realtime":
            if dry_run:
                LOG.info(
                    "Would refresh Stage-IV GRIB2 files for %s from %s/pcpanl.%s/",
                    day,
                    self.settings.stage4_realtime_base_url,
                    day.strftime("%Y%m%d"),
                )
                return 0, 0
            items = self.discover_realtime(day)
            refresh = True
        elif stream == "archive":
            items = [self.archive_file(day)]
            refresh = False
            if dry_run:
                LOG.info("Would download Stage-IV archive %s", items[0].url)
                return 0, 0
        else:
            raise ValueError(f"Unknown Stage-IV stream: {stream}")
        downloaded = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.settings.stage4_download_jobs
        ) as executor:
            futures = [executor.submit(self.download_one, item, refresh=refresh) for item in items]
            for future in futures:
                downloaded += future.result() == "downloaded"
        LOG.info(
            "Complete: Stage-IV %s %s (%d files, %d downloaded)",
            stream,
            day,
            len(items),
            downloaded,
        )
        converter = Stage4Converter(self.settings)
        if stream == "realtime":
            for item in items:
                converter.convert_grib2(item.destination, stream, refresh=True)
        else:
            converter.convert_archive(items[0].destination)
        return len(items), downloaded
