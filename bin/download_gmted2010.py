#!/usr/bin/env python3
"""Download and subset USGS GMTED2010 mean elevation for the NWM CONUS grid."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = (
    "https://edcintl.cr.usgs.gov/downloads/sciweb1/shared/topo/downloads/"
    "GMTED/Grid_ZipFiles/mn30_grd.zip"
)
DEFAULT_ROOT = Path("data/static/dem/gmted2010/mean_30arcsec")
NWM_BOUNDS = (-134.0, 58.0, -60.0, 20.0)  # west, north, east, south
LOG = logging.getLogger(__name__)


def session() -> requests.Session:
    client = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    client.mount("https://", HTTPAdapter(max_retries=retry))
    return client


def download(url: str, destination: Path) -> None:
    if destination.is_file() and zipfile.is_zipfile(destination):
        LOG.info("SKIP valid archive %s", destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    partial.unlink(missing_ok=True)
    LOG.info("GET  %s", url)
    try:
        with session().get(url, stream=True, timeout=(30, 300)) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
        if not zipfile.is_zipfile(partial):
            raise RuntimeError(f"Downloaded response is not a ZIP archive: {url}")
        with zipfile.ZipFile(partial) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise RuntimeError(f"Corrupt GMTED2010 ZIP member: {corrupt}")
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def extract(archive_path: Path, destination: Path) -> Path:
    grid = destination / "mn30_grd"
    if grid.is_dir() and any(grid.iterdir()):
        LOG.info("SKIP extracted archive %s", grid)
        return grid
    temporary = destination.with_name(f"{destination.name}.part")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            root = temporary.resolve()
            for member in archive.infolist():
                resolved = (temporary / member.filename).resolve()
                if not resolved.is_relative_to(root):
                    raise RuntimeError(f"Unsafe ZIP member path: {member.filename}")
            archive.extractall(temporary)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    if not grid.is_dir():
        raise RuntimeError(f"GMTED2010 archive did not contain mn30_grd: {archive_path}")
    return grid


def subset(source: Path, destination: Path, *, force: bool = False) -> None:
    if destination.is_file() and not force:
        LOG.info("SKIP existing subset %s", destination)
        return
    executable = shutil.which("gdal_translate")
    if not executable:
        raise RuntimeError("gdal_translate not found; install/update the hydro-ops environment")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part.tif")
    partial.unlink(missing_ok=True)
    west, north, east, south = NWM_BOUNDS
    LOG.info("SUBSET %s -> %s", source, destination)
    try:
        subprocess.run(
            [
                executable,
                "-projwin",
                str(west),
                str(north),
                str(east),
                str(south),
                "-of",
                "COG",
                "-co",
                "COMPRESS=DEFLATE",
                "-co",
                "LEVEL=6",
                "-co",
                "BIGTIFF=IF_SAFER",
                "-mo",
                f"SOURCE={URL}",
                "-mo",
                "PRODUCT=USGS GMTED2010 mean elevation, 30 arc-seconds",
                "-mo",
                "ELEVATION_UNITS=metres",
                "-mo",
                "VERTICAL_DATUM=EGM96 geoid",
                "-mo",
                "HORIZONTAL_DATUM=WGS84",
                "-mo",
                "AGGREGATION=mean",
                str(source),
                str(partial),
            ],
            check=True,
        )
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--force", action="store_true", help="replace the derived GeoTIFF")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    archive = args.root / "raw/mn30_grd.zip"
    extracted = args.root / "global"
    output = args.root / "gmted2010_mean_30arcsec_nwm_extent.tif"
    download(URL, archive)
    source = extract(archive, extracted)
    subset(source, output, force=args.force)
    print(output)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
