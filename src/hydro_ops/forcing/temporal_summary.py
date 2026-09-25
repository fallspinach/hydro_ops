"""Bounded-memory, UTC-bound-aware daily/monthly forcing summaries."""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import ExitStack
from datetime import date, datetime, timedelta

import numpy as np
from netCDF4 import Dataset, date2num, num2date

from .model_interval import summary_attributes, summary_sources

TIME_UNITS = "hours since 1970-01-01 00:00:00"
ALGORITHM = "utc_endpoint_summary_v2"
MONTHLY_ALGORITHM = "utc_endpoint_summary_v3"


def migration_stable_identity(identity):
    """Normalize ONLY the approved hourly directory insertion, not arbitrary moves."""
    result = dict(identity)
    result["path"] = re.sub(
        r"(/forcing/outputs/[^/]+/(?:retro|nrt|baseline))/hourly/(?=\d{4}/\d{2}/)",
        r"\1/",
        result["path"],
    )
    result["path"] = re.sub(
        r"/(\d{8})\.LDASIN_DOMAIN1\.daily$", r"/\1.FORCING_DAILY.nc", result["path"]
    )
    return result


def summary_path(root, day, monthly=False):
    """New publication name; reuse an existing legacy file during transition."""
    directory = root / (f"{day:%Y}" if monthly else f"{day:%Y/%m}")
    stamp = f"{day:%Y%m}" if monthly else f"{day:%Y%m%d}"
    frequency = "monthly" if monthly else "daily"
    path = directory / f"{stamp}.LDASIN_DOMAIN1.{frequency}"
    legacy = directory / f"{stamp}.FORCING_{frequency.upper()}.nc"
    if path.exists() and legacy.exists():
        raise ValueError(f"Duplicate summary names: {path} and {legacy}")
    return legacy if legacy.exists() else path


def midnight(day):
    return datetime.combine(day, datetime.min.time())


def next_month(day):
    return date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)


def periods(start, end, frequency):
    if start > end:
        raise ValueError("Start must not follow end")
    if frequency not in ("daily", "monthly"):
        raise ValueError("Frequency must be daily or monthly")
    if frequency == "monthly" and (start.day != 1 or end + timedelta(days=1) != next_month(end)):
        raise ValueError("Monthly requests must start on day 1 and end on the last day of a month")
    day = start
    while day <= end:
        stop = day + timedelta(days=1) if frequency == "daily" else next_month(day)
        yield day, stop
        day = stop


def input_path(root, day, daily=False):
    if daily:
        path = summary_path(root, day)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    name = f"{day:%Y%m%d}.LDASIN_DOMAIN1"
    path = root / f"{day:%Y/%m}" / name
    if not path.is_file() and not daily and path.with_name(name + ".nc").is_file():
        path = path.with_name(name + ".nc")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def numeric_time(variable):
    calendar = getattr(variable, "calendar", "standard")
    if calendar not in ("standard", "gregorian", "proleptic_gregorian"):
        raise ValueError(f"Unsupported calendar {calendar}")
    values = np.ma.asarray(variable[:])
    if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
        raise ValueError("Missing or nonfinite time coordinate")
    decoded = num2date(values, variable.units, calendar=calendar)
    return np.asarray(date2num(decoded, TIME_UNITS, calendar="standard"), dtype="f8")


def summarize(
    root,
    output,
    start,
    stop,
    reducers,
    names,
    units,
    *,
    from_daily=False,
    block_rows=120,
    overwrite=False,
    skip_existing=False,
    replace_stale=False,
    domain="",
    stream="",
):
    """Reduce [start 00,stop 00] using completed hourly endpoint samples.

    Daily inputs are allowed only for whole calendar months. Missing spatial
    samples propagate; missing/duplicate time samples and mismatched grids fail.
    """
    if block_rows < 1:
        raise ValueError("block_rows must be positive")
    ndays = (stop - start).days
    monthly = stop == next_month(start) and start.day == 1
    algorithm = MONTHLY_ALGORITHM if monthly else ALGORITHM
    if ndays < 1 or (ndays != 1 and not monthly):
        raise ValueError("Only complete UTC days or calendar months are supported")
    if from_daily and not monthly:
        raise ValueError("Daily inputs are supported only for monthly summaries")
    enabled = {k: v for k, v in reducers.items() if v != "omit"}
    if not enabled or len({names.get(k, k) for k in enabled}) != len(enabled):
        raise ValueError("Reducers must have unique output names and at least one field")
    spec = {"reducers": reducers, "output_names": names, "output_units": units}
    days = [start + timedelta(days=i) for i in range(ndays + (not from_daily))]
    paths = [input_path(root, d, from_daily) for d in days]
    if output.resolve() in [p.resolve() for p in paths]:
        raise ValueError("Cannot overwrite a source file")
    identities = [
        {"path": str(p.resolve()), "bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
        for p in paths
    ]
    signature = hashlib.sha256(
        json.dumps(
            {
                "algorithm": algorithm,
                "inputs": [migration_stable_identity(item) for item in identities],
                "start": str(start),
                "stop": str(stop),
                "from_daily": from_daily,
                "spec": spec,
                "domain": domain,
                "stream": stream,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if output.exists() and not overwrite:
        if skip_existing:
            with Dataset(output) as old:
                if getattr(old, "aggregation_signature", "") == signature:
                    return {"status": "unchanged", "path": str(output)}
            if not replace_stale:
                raise ValueError(f"Stale summary: {output}; regenerate with --overwrite")
        else:
            raise FileExistsError(output)
    with ExitStack() as stack:
        data = [stack.enter_context(Dataset(p)) for p in paths]
        first = data[0]
        ny, nx = len(first.dimensions["y"]), len(first.dimensions["x"])
        t0, t1 = date2num([midnight(start), midnight(stop)], TIME_UNITS)
        records = []
        for d in data:
            if (len(d.dimensions["y"]), len(d.dimensions["x"])) != (ny, nx):
                raise ValueError("Input grid dimensions differ")
            times = numeric_time(d["time"])
            if np.unique(times).size != times.size:
                raise ValueError("Duplicate input timestamps")
            if from_daily:
                if (
                    getattr(d, "aggregation_algorithm", "") != ALGORITHM
                    or json.loads(d.reducer_spec) != spec
                    or getattr(d, "temporal_resolution", "") != "P1D"
                    or getattr(d, "aggregation_sample_count", 0) != 24
                    or len(times) != 1
                ):
                    raise ValueError("Monthly input must be a verified compatible daily summary")
                i = len(records)
                bounds = numeric_time(d["time_bounds"]).reshape(-1)
                if (
                    not np.array_equal(bounds, [t0 + 24 * i, t0 + 24 * (i + 1)])
                    or times[0] != t0 + 24 * i + 12
                ):
                    raise ValueError("Daily summary bounds or midpoint mismatch")
                records.append((d, np.array([0]), times))
            else:
                keep = np.flatnonzero((times > t0) & (times <= t1))
                if len(keep):
                    records.append((d, keep, times[keep]))
        actual = np.concatenate([r[2] for r in records]) if records else np.array([])
        expected = np.arange(t0 + 12, t1, 24) if from_daily else np.arange(t0 + 1, t1 + 1)
        if not np.array_equal(actual, expected):
            raise ValueError("Missing, duplicate, off-hour or out-of-order required timestamps")
        static_names = [
            k
            for k, v in first.variables.items()
            if "time" not in v.dimensions and k != "time_bounds"
        ]
        for name in ("lat", "lon", "x", "y", "subset_model_mask", "subset_forcing_mask"):
            if name not in first.variables:
                continue
            for d in data[1:]:
                if name not in d.variables or d[name].dimensions != first[name].dimensions:
                    raise ValueError(f"Grid metadata mismatch: {name}")
                if "y" in first[name].dimensions:
                    axis = first[name].dimensions.index("y")
                    for y in range(0, ny, block_rows):
                        sl = [slice(None)] * first[name].ndim
                        sl[axis] = slice(y, y + block_rows)
                        np.testing.assert_array_equal(first[name][tuple(sl)], d[name][tuple(sl)])
                else:
                    np.testing.assert_array_equal(first[name][:], d[name][:])
        for source, method in enabled.items():
            keys = (names.get(source, source),) if from_daily else summary_sources(source)
            key = keys[0]
            for d in data:
                for component in keys:
                    if component not in d.variables or d[component].dimensions != (
                        "time",
                        "y",
                        "x",
                    ):
                        raise ValueError(f"Missing/unsupported forcing field {component}")
                    if getattr(d[component], "units", None) != getattr(first[key], "units", None):
                        raise ValueError(f"Inconsistent units for {component}")
                    d[component].set_var_chunk_cache(2 * 1024 * 1024, 1009, 0.5)
            if (
                method == "integral"
                and source == "RAINRATE"
                and not from_daily
                and getattr(first[key], "units", "")
                not in (
                    "kg m-2 s-1",
                    "kg m^-2 s^-1",
                    "kg/m^2/s",
                    "kg m**-2 s**-1",
                    "mm s-1",
                    "mm/s",
                )
            ):
                raise ValueError(
                    "RAINRATE must be an hourly precipitation rate in kg m-2 s-1 or mm s-1"
                )
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
        if temporary.exists():
            raise FileExistsError(temporary)
        try:
            with Dataset(temporary, "w", format="NETCDF4") as out:
                for name, dim in first.dimensions.items():
                    out.createDimension(name, 1 if name == "time" else len(dim))
                if "bounds" not in out.dimensions:
                    out.createDimension("bounds", 2)
                t = out.createVariable("time", "f8", ("time",))
                t.units = TIME_UNITS
                t.calendar = "standard"
                t.standard_name = "time"
                t.bounds = "time_bounds"
                t[:] = [(t0 + t1) / 2]
                b = out.createVariable("time_bounds", "f8", ("time", "bounds"))
                b.units = TIME_UNITS
                b.calendar = "standard"
                b[:] = [[t0, t1]]
                for name in static_names:
                    v = first[name]
                    kwargs = (
                        {"fill_value": v.getncattr("_FillValue")}
                        if "_FillValue" in v.ncattrs()
                        else {}
                    )
                    dest = out.createVariable(
                        name, v.datatype, v.dimensions, zlib=bool(v.ndim), complevel=2, **kwargs
                    )
                    dest.setncatts({k: v.getncattr(k) for k in v.ncattrs() if k != "_FillValue"})
                    if "y" in v.dimensions:
                        axis = v.dimensions.index("y")
                        for y in range(0, ny, block_rows):
                            sl = [slice(None)] * v.ndim
                            sl[axis] = slice(y, y + block_rows)
                            dest[tuple(sl)] = v[tuple(sl)]
                    else:
                        dest[...] = v[...]
                for source, method in enabled.items():
                    key = names.get(source, source) if from_daily else summary_sources(source)[0]
                    daily_extrema_mean = monthly and (
                        (source == "T2D_MIN" and method == "minimum")
                        or (source == "T2D_MAX" and method == "maximum")
                    )
                    dest = out.createVariable(
                        names.get(source, source),
                        "f4",
                        ("time", "y", "x"),
                        fill_value=np.float32(-9999),
                        zlib=True,
                        complevel=2,
                        shuffle=True,
                        chunksizes=(1, min(block_rows, ny), min(120, nx)),
                    )
                    dest.setncatts(
                        {
                            k: first[key].getncattr(k)
                            for k in (
                                "units",
                                "long_name",
                                "standard_name",
                                "grid_mapping",
                                "coordinates",
                            )
                            if k in first[key].ncattrs()
                        }
                    )
                    if source in units:
                        dest.units = units[source]
                    dest.setncatts(summary_attributes(source))
                    dest.cell_methods = (
                        f"time: {method}"
                        if method != "integral"
                        else "time: sum (hourly rate integrated over 3600 s)"
                    )
                    if daily_extrema_mean:
                        dest.cell_methods = f"time: {method} within days time: mean over days"
                        dest.long_name = f"Monthly mean of daily {method} hourly air temperature"
                    if method == "integral" and source == "RAINRATE":
                        dest.standard_name = "precipitation_amount"
                        dest.long_name = (
                            "UTC calendar-month precipitation amount"
                            if monthly
                            else "UTC-day precipitation amount"
                        )
                    if method == "first":
                        dest.cell_methods = "time: point"
                        dest.selection_method = "first endpoint"
                    if method == "last":
                        dest.cell_methods = "time: point"
                        dest.selection_method = "last endpoint"
                    for y in range(0, ny, block_rows):
                        height = min(block_rows, ny - y)
                        total = np.zeros((height, nx), "f8")
                        missing = np.zeros((height, nx), bool)
                        count = 0
                        day_extreme = None
                        for d, indexes, _ in records:
                            for index in indexes:
                                values = np.ma.asarray(
                                    d[key][int(index), y : y + height, :], dtype="f8"
                                )
                                if source == "WIND_SPEED" and not from_daily:
                                    other = np.ma.asarray(
                                        d["V2D"][int(index), y : y + height, :], dtype="f8"
                                    )
                                    values = np.ma.array(
                                        np.hypot(values.filled(np.nan), other.filled(np.nan)),
                                        mask=np.ma.getmaskarray(values) | np.ma.getmaskarray(other),
                                    )
                                bad = np.ma.getmaskarray(values) | ~np.isfinite(
                                    np.ma.getdata(values)
                                )
                                missing |= bad
                                v = np.ma.filled(values, np.nan)
                                if daily_extrema_mean:
                                    if from_daily:
                                        total += v
                                    else:
                                        operation = (
                                            np.minimum if method == "minimum" else np.maximum
                                        )
                                        day_extreme = (
                                            v.copy()
                                            if count % 24 == 0
                                            else operation(day_extreme, v)
                                        )
                                        if count % 24 == 23:
                                            total += day_extreme
                                elif method in ("mean", "sum", "integral"):
                                    total += v
                                elif method == "minimum":
                                    total = v.copy() if count == 0 else np.minimum(total, v)
                                elif method == "maximum":
                                    total = v.copy() if count == 0 else np.maximum(total, v)
                                elif (method == "first" and count == 0) or method == "last":
                                    total = v.copy()
                                elif method not in ("first",):
                                    raise ValueError(f"Unsupported reducer {method}")
                                count += 1
                        if method == "mean":
                            total /= count
                        if daily_extrema_mean:
                            total /= ndays
                        if method == "integral" and not from_daily:
                            total *= 3600
                        dest[0, y : y + height, :] = np.ma.array(total, mask=missing)
                out.setncatts(
                    {
                        "Conventions": "CF-1.8",
                        "aggregation_algorithm": algorithm,
                        "aggregation_signature": signature,
                        "reducer_spec": json.dumps(spec, sort_keys=True),
                        "temporal_resolution": "P1M" if monthly else "P1D",
                        "day_definition": "model_interval",
                        "interval_start_utc": midnight(start).isoformat() + "Z",
                        "interval_end_utc": midnight(stop).isoformat() + "Z",
                        "sample_endpoint_start_utc": (
                            midnight(start) + timedelta(hours=1)
                        ).isoformat()
                        + "Z",
                        "sample_endpoint_end_utc": midnight(stop).isoformat() + "Z",
                        "aggregation_sample_count": ndays * 24,
                        "aggregation_sample_interval": "PT1H",
                        "input_resolution": "daily" if from_daily else "hourly",
                        "source_files": json.dumps(identities),
                        "domain": domain,
                        "forcing_stream": stream,
                        "spatial_missing_policy": "propagate any missing/nonfinite sample; never count missing as zero",
                        "vector_wind_note": "WIND_SPEED averages hourly sqrt(U2D**2+V2D**2); optional U2D/V2D are vector means",
                        "publication_role": "temporal summary, not an hourly LDASIN input",
                    }
                )
            with Dataset(temporary) as check:
                if numeric_time(check["time_bounds"]).tolist() != [[t0, t1]]:
                    raise ValueError("Output bounds failed readback")
            for p, identity in zip(paths, identities, strict=True):
                if (
                    p.stat().st_size != identity["bytes"]
                    or p.stat().st_mtime_ns != identity["mtime_ns"]
                ):
                    raise ValueError(f"Source changed during reduction: {p}")
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "status": "published",
        "path": str(output),
        "samples": ndays * 24,
        "input_files": len(paths),
    }
