"""Opt-in GFS short-forecast surface forcing, with explicit accumulation windows."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.download.hrrr import parse_index

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
FIELDS = {
    "T2D": ("TMP", "2 m above ground"), "Q2D": ("SPFH", "2 m above ground"),
    "PSFC": ("PRES", "surface"), "SWDOWN": ("DSWRF", "surface"),
    "LWDOWN": ("DLWRF", "surface"), "U2D": ("UGRD", "10 m above ground"),
    "V2D": ("VGRD", "10 m above ground"), "precipitation_depth": ("APCP", "surface"),
    "elevation": ("HGT", "surface"),
}
ACCUMULATED = {"precipitation_depth": "acc", "SWDOWN": "ave", "LWDOWN": "ave"}


def cycle_candidates(valid: datetime, maximum_lead: int = 12):
    if valid.tzinfo is None or valid.minute or valid.second or valid.microsecond:
        raise ValueError("Valid time must be an aware whole UTC hour")
    valid = valid.astimezone(UTC)
    previous = valid - timedelta(hours=1)
    cycle = previous.replace(hour=previous.hour // 6 * 6)
    while (lead := int((valid - cycle).total_seconds() / 3600)) <= maximum_lead:
        yield cycle, lead
        cycle -= timedelta(hours=6)


def select(records, name, lead):
    parameter, level = FIELDS[name]
    choices = [r for r in records if (r.variable, r.level) == (parameter, level)]
    if name in ACCUMULATED:
        windows = []
        for record in choices:
            match = re.fullmatch(r"(\d+)-(\d+) hour (acc|ave) fcst", record.timing)
            if match and int(match[2]) == lead and match[3] == ACCUMULATED[name]:
                windows.append((int(match[1]), record))
        if not windows:
            raise ValueError(f"No supported {name} window ending at {lead}")
        # GFS publishes bucket and cycle-total APCP. Prefer the shortest window;
        # where those coincide, choose the first (bucket) message consistently.
        start, record = max(windows, key=lambda pair: (pair[0], -pair[1].number))
        if start >= lead:
            raise ValueError("Zero/negative statistical interval")
        return record, (start, lead)
    choices = [r for r in choices if r.timing == f"{lead} hour fcst"]
    if len(choices) != 1:
        raise ValueError(f"Ambiguous/missing instantaneous {name}")
    return choices[0], None


def hourly_statistic(current, window, previous=None, previous_window=None, *, average=False):
    start, end = window
    total = np.asarray(current, dtype=np.float64) * (end - start if average else 1)
    if end - start == 1:
        return total
    if previous is None or previous_window != (start, end - 1):
        raise ValueError("Cannot difference across statistical-window or cycle boundaries")
    return total - np.asarray(previous) * (end - start - 1 if average else 1)


class GfsDownloader:
    def __init__(self, cache: Path, work: Path, *, wgrib2="wgrib2"):
        self.cache, self.work, self.wgrib2 = cache, work, wgrib2
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def forecast(self, cycle, lead):
        directory = self.cache / f"{cycle:%Y%m%d%H}"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"f{lead:03}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._forecast(cycle, lead)

    def _forecast(self, cycle, lead):
        path = self.cache / f"{cycle:%Y%m%d%H}/f{lead:03}.nc"
        if path.exists():
            with xr.open_dataset(path) as cached:
                if cached.attrs.get("cycle") != cycle.isoformat() or cached.attrs.get("lead") != lead:
                    raise ValueError("Cached forecast identity mismatch")
                result = cached.load()
                result.attrs["lead"] = int(result.attrs["lead"])
                return result
        url = f"{BASE}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{lead:03}"
        response = self.session.get(url + ".idx", timeout=(15, 90))
        response.raise_for_status()
        records = parse_index(response.text)
        chosen = {name: select(records, name, lead) for name in FIELDS}
        self.work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gfs-", dir=self.work) as temporary:
            grib, nc = Path(temporary) / "fields.grib2", Path(temporary) / "fields.nc"
            remote_identity = None
            with grib.open("wb") as stream:
                for record, _ in chosen.values():
                    if record.end is None:
                        raise ValueError("Unbounded GRIB byte range")
                    block = self.session.get(url, headers={"Range": f"bytes={record.offset}-{record.end}"}, timeout=(15, 90))
                    block.raise_for_status()
                    if (block.status_code != 206
                            or not block.headers.get("Content-Range", "").startswith(f"bytes {record.offset}-{record.end}/")
                            or len(block.content) != record.end - record.offset + 1
                            or not block.content.startswith(b"GRIB") or not block.content.endswith(b"7777")):
                        raise ValueError("Server returned an invalid GRIB byte range")
                    identity = (block.headers.get("ETag"), block.headers.get("Last-Modified"))
                    if remote_identity is not None and identity != remote_identity:
                        raise ValueError("GFS object changed during subset download")
                    remote_identity = identity
                    stream.write(block.content)
            subprocess.run([self.wgrib2, str(grib), "-netcdf", str(nc)], check=True, capture_output=True)
            with xr.open_dataset(nc) as raw:
                expected_time = np.datetime64((cycle + timedelta(hours=lead)).replace(tzinfo=None), "ns")
                if raw.sizes.get("time") != 1 or raw.time.values[0] != expected_time:
                    raise ValueError("GRIB decoded valid time differs from requested cycle/lead")
                # Regional output with a generous remapping buffer; byte-range
                # transfer downloads selected global messages, not full GRIB files.
                lat, lon = raw.latitude.values, ((raw.longitude.values + 180) % 360) - 180
                iy = np.flatnonzero((lat >= 24) & (lat <= 54))
                ix = np.flatnonzero((lon >= -126) & (lon <= -66))
                arrays = {}
                for name, (parameter, level) in FIELDS.items():
                    variable = parameter + "_" + level.replace(" ", "")
                    values = np.asarray(raw[variable].isel(time=0).values)[np.ix_(iy, ix)]
                    arrays[name] = (("lat", "lon"), values)
                result = xr.Dataset(arrays, coords={"lat": lat[iy], "lon": lon[ix]}).sortby("lat").sortby("lon").load()
        if any(not np.isfinite(v.values).all() for v in result.data_vars.values()):
            raise ValueError("GFS subset contains missing source values")
        result.attrs.update(cycle=cycle.isoformat(), lead=lead, url=url,
                            retrieved_utc=datetime.now(UTC).isoformat(),
                            remote_last_modified=remote_identity[1] or "",
                            remote_etag=remote_identity[0] or "",
                            windows=json.dumps({k: w for k, (_, w) in chosen.items() if w is not None}),
                            selected_records=json.dumps({k: r.number for k, (r, _) in chosen.items()}))
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part.nc")
        result.to_netcdf(partial, encoding={n: {"zlib": True, "complevel": 2} for n in result.data_vars})
        os.replace(partial, path)
        return result

    def hour(self, valid, *, as_of=None):
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        def available(dataset):
            timestamp = dataset.attrs.get("remote_last_modified")
            return (parsedate_to_datetime(timestamp) if timestamp
                    else datetime.fromisoformat(dataset.attrs["retrieved_utc"]))

        errors = []
        for cycle, lead in cycle_candidates(valid):
            try:
                current = self.forecast(cycle, lead)
                if as_of is not None and available(current) > as_of:
                    raise ValueError("No evidence this forecast was available by the requested as-of time")
                windows = json.loads(current.attrs["windows"])
                prior = self.forecast(cycle, lead - 1) if any(b - a > 1 for a, b in windows.values()) else None
                if prior is not None:
                    np.testing.assert_array_equal(current.lat, prior.lat)
                    np.testing.assert_array_equal(current.lon, prior.lon)
                    if as_of is not None and available(prior) > as_of:
                        raise ValueError("Prior accumulator unavailable at requested as-of time")
                result = current.copy(deep=True)
                clipped = {}
                for name, statistic in ACCUMULATED.items():
                    old_window = tuple(json.loads(prior.attrs["windows"])[name]) if prior is not None else None
                    values = hourly_statistic(current[name].values, tuple(windows[name]),
                        prior[name].values if prior is not None else None, old_window,
                        average=statistic == "ave")
                    tolerance = 0.05 if name == "precipitation_depth" else 2.0
                    if np.any(values < -tolerance):
                        raise ValueError(f"Material negative hourly {name}: {values.min()}")
                    clipped[name] = int((values < 0).sum())
                    result[name].values = np.maximum(values, 0).astype(np.float32)
                result.attrs.update(valid_time=valid.isoformat(), interval="(valid_time - 1 hour, valid_time]",
                                    negative_roundoff_clipped=json.dumps(clipped),
                                    mode="operational" if as_of else "historical_short_forecast_experiment_not_asof_replay")
                return result
            except (requests.RequestException, ValueError) as error:
                errors.append(f"{cycle.isoformat()} f{lead:03}: {error}")
        raise RuntimeError("No complete GFS bundle within 12-hour lead: " + "; ".join(errors))
