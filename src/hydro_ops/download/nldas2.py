"""NASA GES DISC NLDAS-2 primary hourly forcing downloader."""

from __future__ import annotations

import concurrent.futures
import http.cookiejar
import logging
import netrc
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.config import Settings
from hydro_ops.download.http import apply_remote_mtime, local_matches_remote

LOG = logging.getLogger(__name__)
NETCDF_MAGICS = (b"CDF\x01", b"CDF\x02", b"\x89HDF\r\n\x1a\n")


@dataclass(frozen=True)
class Granule:
    url: str
    destination: Path


def iter_dates(start: date, end: date):
    if start > end:
        raise ValueError("Start date is after end date")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def is_netcdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            magic = stream.read(8)
    except OSError:
        return False
    return any(magic.startswith(signature) for signature in NETCDF_MAGICS)


class Nldas2Downloader:
    def __init__(self, settings: Settings, *, check_credentials: bool = True):
        self.settings = settings
        self._cookie_lock = threading.Lock()
        if check_credentials:
            self._check_credentials()

    def _check_credentials(self) -> None:
        path = self.settings.nldas_netrc
        if not path.is_file():
            raise RuntimeError(f"Earthdata credentials not found: {path}")
        if path.stat().st_mode & 0o077:
            LOG.warning("Credentials permissions are not 600: %s", path)
        try:
            authenticators = netrc.netrc(path).authenticators("urs.earthdata.nasa.gov")
        except (netrc.NetrcParseError, OSError) as error:
            raise RuntimeError(f"Could not parse Earthdata credentials: {error}") from error
        if not authenticators:
            raise RuntimeError(f"No urs.earthdata.nasa.gov entry found in {path}")
        self.settings.nldas_cookies.parent.mkdir(parents=True, exist_ok=True)
        self.settings.nldas_cookies.touch(mode=0o600, exist_ok=True)
        self.settings.nldas_cookies.chmod(0o600)
        os.environ["NETRC"] = str(path)

    def _session(self) -> requests.Session:
        session = requests.Session()
        jar = http.cookiejar.MozillaCookieJar(str(self.settings.nldas_cookies))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (FileNotFoundError, http.cookiejar.LoadError):
            pass
        session.cookies = jar
        retry = Retry(
            total=self.settings.nldas_retries,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def _save_cookies(self, session: requests.Session) -> None:
        if isinstance(session.cookies, http.cookiejar.FileCookieJar):
            with self._cookie_lock:
                session.cookies.save(ignore_discard=True, ignore_expires=True)

    @property
    def timeout(self) -> tuple[int, int]:
        return self.settings.nldas_connect_timeout, self.settings.nldas_read_timeout

    def discover(self, day: date) -> list[Granule]:
        year, doy, stamp = day.strftime("%Y"), day.strftime("%j"), day.strftime("%Y%m%d")
        remote_dir = f"{self.settings.nldas_base_url}/{year}/{doy}/"
        local_dir = self.settings.nldas_data_dir / year / doy
        pattern = re.compile(rf"NLDAS_FORA0125_H\.A{stamp}\.[0-9]{{4}}\.[A-Za-z0-9.]+\.nc4?")
        with self._session() as session:
            response = session.get(remote_dir, timeout=self.timeout)
            response.raise_for_status()
            self._save_cookies(session)
            names = sorted(set(pattern.findall(response.text)))
        if not names:
            raise RuntimeError(
                f"No NetCDF granules found for {day} at {remote_dir} "
                "(not published yet, or authentication failed)"
            )
        return [Granule(urljoin(remote_dir, name), local_dir / name) for name in names]

    def download_one(self, granule: Granule) -> str:
        destination = granule.destination
        if is_netcdf(destination):
            with self._session() as session:
                response = session.head(granule.url, timeout=self.timeout)
                response.raise_for_status()
                self._save_cookies(session)
            if local_matches_remote(destination, response.headers):
                LOG.info("SKIP current %s", destination)
                return "skipped"
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        LOG.info("GET  %s", granule.url)
        try:
            with self._session() as session:
                with session.get(granule.url, stream=True, timeout=self.timeout) as response:
                    response.raise_for_status()
                    with partial.open("wb") as stream:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                stream.write(chunk)
                self._save_cookies(session)
            if not is_netcdf(partial):
                raise RuntimeError(f"Downloaded response is not NetCDF: {granule.url}")
            partial.replace(destination)
            apply_remote_mtime(destination, response.headers)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return "downloaded"

    def download_day(self, day: date, dry_run: bool = False) -> tuple[int, int]:
        year, doy, stamp = day.strftime("%Y"), day.strftime("%j"), day.strftime("%Y%m%d")
        if dry_run:
            LOG.info(
                "Would discover and download NLDAS_FORA0125_H.A%s.*.nc* from %s/%s/%s/ to %s",
                stamp,
                self.settings.nldas_base_url,
                year,
                doy,
                self.settings.nldas_data_dir / year / doy,
            )
            return 0, 0
        granules = self.discover(day)
        downloaded = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.settings.nldas_download_jobs
        ) as executor:
            for result in executor.map(self.download_one, granules):
                downloaded += result == "downloaded"
        LOG.info("Complete: %s (%d files, %d downloaded)", day, len(granules), downloaded)
        return len(granules), downloaded
