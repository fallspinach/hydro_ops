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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.config import Settings
from hydro_ops.download.http import apply_remote_mtime, local_matches_remote
from hydro_ops.forcing.daily_archive import create_daily_archive, verified_daily_archive
from hydro_ops.work import temporary_work_root

LOG = logging.getLogger(__name__)
NETCDF_MAGICS = (b"CDF\x01", b"CDF\x02", b"\x89HDF\r\n\x1a\n")
CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
CMR_COLLECTION = "C2033151148-GES_DISC"  # NLDAS_FORA0125_H, version 2.0


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
    def __init__(self, settings: Settings, *, check_credentials: bool = True,
                 discovery: str | None = None):
        self.settings = settings
        self.discovery = discovery or getattr(settings, "nldas_discovery", "legacy")
        if self.discovery not in {"legacy", "cmr"}:
            raise ValueError(f"Unknown NLDAS discovery backend: {self.discovery}")
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

    def discover(self, day: date, *, allow_unpublished: bool = False) -> list[Granule]:
        if self.discovery == "cmr":
            return self.discover_cmr(day, allow_unpublished=allow_unpublished)
        year, doy, stamp = day.strftime("%Y"), day.strftime("%j"), day.strftime("%Y%m%d")
        remote_dir = f"{self.settings.nldas_base_url}/{year}/{doy}/"
        local_dir = self.settings.nldas_data_dir / year / doy
        pattern = re.compile(rf"NLDAS_FORA0125_H\.A{stamp}\.[0-9]{{4}}\.[A-Za-z0-9.]+\.nc4?")
        with self._session() as session:
            response = session.get(remote_dir, timeout=self.timeout)
            if allow_unpublished and response.status_code == 404:
                LOG.warning("NLDAS-2 %s not yet published (HTTP 404); retry next refresh", day)
                return []
            response.raise_for_status()
            self._save_cookies(session)
            names = sorted(set(pattern.findall(response.text)))
        if not names:
            raise RuntimeError(
                f"No NetCDF granules found for {day} at {remote_dir} "
                "(not published yet, or authentication failed)"
            )
        return [Granule(urljoin(remote_dir, name), local_dir / name) for name in names]

    def discover_cmr(self, day: date, *, allow_unpublished: bool = False) -> list[Granule]:
        """Discover exact-hour cloud granules; never synthesize cloud URLs."""
        params = {"collection_concept_id": CMR_COLLECTION, "page_size": 100,
                  "temporal": f"{day}T00:00:00Z,{day}T23:59:59Z",
                  "sort_key[]": ["start_date", "producer_granule_id"]}
        granules = {}
        seen_ids = set()
        page = 1
        with self._session() as session:
            while True:
                response = session.get(CMR_URL, params={**params, "page_num": page}, timeout=self.timeout)
                response.raise_for_status()  # CMR errors are not unpublished days.
                entries = response.json()["feed"]["entry"]
                for entry in entries:
                    if entry["id"] in seen_ids:
                        raise ValueError("CMR repeated granule across pages")
                    seen_ids.add(entry["id"])
                    if entry.get("collection_concept_id") != CMR_COLLECTION:
                        raise ValueError("Unexpected NLDAS CMR collection")
                    name = entry["producer_granule_id"]
                    match = re.fullmatch(rf"NLDAS_FORA0125_H\.A{day:%Y%m%d}\.([0-9]{{2}})00\.020\.nc", name)
                    if not match or int(match[1]) > 23:
                        raise ValueError(f"Unexpected NLDAS CMR filename: {name}")
                    valid = datetime.fromisoformat(entry["time_start"])
                    expected = datetime(day.year, day.month, day.day, int(match[1]), tzinfo=UTC)
                    if valid != expected:
                        raise ValueError(f"CMR time/filename mismatch: {name}")
                    urls = set()
                    for link in entry.get("links", []):
                        url = link.get("href", "")
                        parsed = urlparse(url)
                        if (not link.get("inherited") and link.get("rel", "").endswith("/data#")
                                and parsed.scheme == "https"
                                and parsed.netloc == "data.gesdisc.earthdata.nasa.gov"
                                and Path(parsed.path).name == name):
                            urls.add(url)
                    if len(urls) != 1 or name in granules:
                        raise ValueError(f"Missing/ambiguous NLDAS cloud granule: {name}")
                    granules[name] = Granule(urls.pop(), self.settings.nldas_data_dir / day.strftime("%Y/%j") / name)
                if len(entries) < 100:
                    if "CMR-Hits" in response.headers and int(response.headers["CMR-Hits"]) != len(seen_ids):
                        raise ValueError("CMR result count changed/incomplete; retry discovery")
                    break
                page += 1
        if not granules:
            if not allow_unpublished:
                raise RuntimeError(f"No NLDAS-2 cloud granules published for {day}")
            LOG.warning("NLDAS-2 %s not yet indexed by CMR; retry next refresh", day)
        LOG.info("CMR NLDAS-2 %s: %d cloud granules", day, len(granules))
        return [granules[name] for name in sorted(granules)]

    def download_one(self, granule: Granule) -> str:
        destination = granule.destination
        if is_netcdf(destination):
            with self._session() as session:
                response = session.head(granule.url, timeout=self.timeout, allow_redirects=True)
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

    def download_day(self, day: date, dry_run: bool = False, *,
                     allow_unpublished: bool = False, aggregate_complete: bool = False) -> tuple[int, int]:
        year, doy, stamp = day.strftime("%Y"), day.strftime("%j"), day.strftime("%Y%m%d")
        if dry_run:
            if self.discovery == "cmr":
                LOG.info("Would discover NLDAS-2 %s via CMR collection %s and download cloud HTTPS links to %s",
                         day, CMR_COLLECTION, self.settings.nldas_data_dir / year / doy)
                return 0, 0
            LOG.info(
                "Would discover and download NLDAS_FORA0125_H.A%s.*.nc* from %s/%s/%s/ to %s",
                stamp,
                self.settings.nldas_base_url,
                year,
                doy,
                self.settings.nldas_data_dir / year / doy,
            )
            return 0, 0
        daily = (
            self.settings.nldas_data_dir / year /
            f"NLDAS_FORA0125_H.A{stamp}.020.nc"
        )
        if is_netcdf(daily) and verified_daily_archive(daily, day):
            LOG.info("SKIP verified daily archive %s", daily)
            return 24, 0
        granules = self.discover(day, allow_unpublished=allow_unpublished)
        if not granules:
            return 0, 0
        downloaded = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.settings.nldas_download_jobs
        ) as executor:
            for result in executor.map(self.download_one, granules):
                downloaded += result == "downloaded"
        LOG.info("Complete: %s (%d files, %d downloaded)", day, len(granules), downloaded)
        if aggregate_complete:
            if len(granules) == 24:
                create_daily_archive(
                    [g.destination for g in granules], daily, day,
                    compression_level=2,
                    work_directory=temporary_work_root(self.settings, "daily-nldas2"),
                )
                LOG.info("Archived complete NLDAS-2 day %s; hourly inputs retained", day)
            else:
                LOG.info("Partial NLDAS-2 day %s: %d hours retained; daily aggregation deferred",
                         day, len(granules))
        return len(granules), downloaded
