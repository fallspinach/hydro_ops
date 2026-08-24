#!/usr/bin/env python3
"""Download the public NCEP NWM 3.1 CONUS full-routing input bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

PACKAGE = "nwm.v3.1.6"
BASE_URL = f"https://www.nco.ncep.noaa.gov/pmb/codes/nwprod/{PACKAGE}/parm"
FILES = {
    "domain": (
        "Diversion_CONUS.nc",
        "Fulldom_CONUS_FullRouting.nc",
        "GEOGRID_LDASOUT_Spatial_Metadata_CONUS.nc",
        "GWBUCKPARM_CONUS_FullRouting.nc",
        "RouteLink_CONUS.nc",
        "geo_em_CONUS.nc",
        "hydro2dtbl_CONUS_FullRouting.nc",
        "nudgingParams_CONUS.nc",
        "reservoir_index_AnA.nc",
        "reservoir_index_Extended_AnA.nc",
        "reservoir_index_Medium_Range.nc",
        "reservoir_index_Short_Range.nc",
        "soilproperties_CONUS_FullRouting.nc",
        "spatialweights_CONUS_FullRouting.nc",
        "wrfinput_CONUS.nc",
    ),
    "constants": ("CHANPARM.TBL", "GENPARM.TBL", "HYDRO.TBL", "MPTABLE.TBL", "SOILPARM.TBL"),
    "analysis_assim": ("hydro.namelist", "namelist.hrldas"),
}


def remote_size(session: requests.Session, url: str) -> int:
    response = session.head(url, allow_redirects=True, timeout=(20, 60))
    response.raise_for_status()
    value = response.headers.get("Content-Length")
    if value is None:
        raise RuntimeError(f"Server did not provide Content-Length: {url}")
    return int(value)


def download(session: requests.Session, url: str, destination: Path, expected: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected:
        raise RuntimeError(f"Partial file exceeds remote size: {partial}")
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with session.get(url, headers=headers, stream=True, timeout=(20, 300)) as response:
        response.raise_for_status()
        if offset and response.status_code != 206:
            offset = 0
            partial.unlink(missing_ok=True)
        mode = "ab" if offset else "wb"
        with partial.open(mode) as stream:
            shutil.copyfileobj(response.raw, stream, length=8 * 1024 * 1024)
    if partial.stat().st_size != expected:
        raise RuntimeError(
            f"Incomplete download {partial}: {partial.stat().st_size} of {expected} bytes"
        )
    partial.replace(destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/static/nwm/operational/nwm.v3.1.6"),
    )
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    records = []
    complete = True
    with requests.Session() as session:
        session.headers["User-Agent"] = "hydro-ops/0.1 NWM-input-downloader"
        for group, names in FILES.items():
            for name in names:
                url = f"{BASE_URL}/{group}/{name}"
                destination = args.output / group / name
                expected = remote_size(session, url)
                valid = destination.is_file() and destination.stat().st_size == expected
                print(
                    f"{'READY' if valid else 'MISSING':7s} {group}/{name} "
                    f"{destination.stat().st_size if destination.exists() else 0:,}/{expected:,}",
                    flush=True,
                )
                if not valid and not args.status_only:
                    download(session, url, destination, expected)
                    valid = True
                complete &= valid
                if valid and not args.status_only:
                    records.append(
                        {
                            "path": f"{group}/{name}",
                            "url": url,
                            "bytes": expected,
                            "sha256": sha256(destination),
                        }
                    )
    if not args.status_only:
        manifest = {
            "created": datetime.now(UTC).isoformat(),
            "package": PACKAGE,
            "base_url": BASE_URL,
            "files": records,
            "known_external_requirements": [
                "LAKEPARM_CONUS.nc (operational dynamic parameter store; not in public parm/domain)",
                "matching land and hydro restart pair (operational cycling store; not disseminated)",
            ],
        }
        partial = args.output / "manifest.json.part"
        partial.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        partial.replace(args.output / "manifest.json")
    return 0 if complete or not args.status_only else 1


if __name__ == "__main__":
    sys.exit(main())
