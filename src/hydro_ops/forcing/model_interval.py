"""UTC model-interval reductions from calendar-chunked hourly forcing."""

from __future__ import annotations

import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

METHODS = {"mean", "sum", "minimum", "maximum", "first", "last", "integral", "omit"}


def load_forcing_reducers(path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    reducers = dict(document["FORCING"])
    unknown = set(reducers.values()) - METHODS
    if unknown:
        raise ValueError(f"Unknown forcing reducers: {sorted(unknown)}")
    return reducers, dict(document.get("output_names", {})), dict(document.get("output_units", {}))


def expected_endpoint_times(day: date) -> np.ndarray:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return np.asarray(
        [np.datetime64((start + timedelta(hours=hour)).replace(tzinfo=None), "ns") for hour in range(1, 25)]
    )


def _reduce(array: xr.DataArray, method: str, interval_seconds: float) -> xr.DataArray:
    if method == "mean":
        return array.mean("time", skipna=False)
    if method == "sum":
        return array.sum("time", skipna=False)
    if method == "minimum":
        return array.min("time", skipna=False)
    if method == "maximum":
        return array.max("time", skipna=False)
    if method == "first":
        return array.isel(time=0, drop=True)
    if method == "last":
        return array.isel(time=-1, drop=True)
    if method == "integral":
        return array.sum("time", skipna=False) * interval_seconds
    raise ValueError(f"Unsupported forcing reducer: {method}")


def reduce_model_interval_forcing(
    paths: list[Path],
    day: date,
    reducers: dict[str, str],
    *,
    output_names: dict[str, str] | None = None,
    output_units: dict[str, str] | None = None,
) -> xr.Dataset:
    """Reduce exactly D 01 through D+1 00 from one or more calendar chunks."""
    if not paths:
        raise ValueError("At least one forcing collection is required")
    output_names = output_names or {}
    output_units = output_units or {}
    opened = [xr.open_dataset(path, mask_and_scale=True, chunks={"time": 1}) for path in paths]
    try:
        combined = xr.concat(
            opened, dim="time", data_vars="minimal", coords="minimal", compat="override"
        ).sortby("time")
        times = np.asarray(combined.time.values, dtype="datetime64[ns]")
        if len(np.unique(times)) != len(times):
            raise ValueError("Duplicate forcing timestamps were supplied")
        expected = expected_endpoint_times(day)
        present = set(times.astype(np.int64).tolist())
        missing = [str(value) for value in expected if int(value.astype(np.int64)) not in present]
        if missing:
            raise ValueError(f"Missing model-interval forcing timestamps: {missing}")
        selected = combined.sel(time=expected)
        selected_times = np.asarray(selected.time.values, dtype="datetime64[ns]")
        if not np.array_equal(selected_times, expected):
            raise ValueError("Forcing timestamps are not in the expected model-interval order")
        intervals = np.diff(expected).astype("timedelta64[s]").astype(np.int64)
        if not np.all(intervals == 3600):
            raise ValueError("Model-interval forcing is not hourly")

        start = expected[0] - np.timedelta64(1, "h")
        end = expected[-1]
        midpoint = start + (end - start) // 2
        variables: dict[str, xr.DataArray] = {}
        for name, method in reducers.items():
            if method == "omit":
                continue
            if name not in selected:
                raise ValueError(f"Configured forcing variable is absent: {name}")
            value = _reduce(selected[name], method, 3600.0).expand_dims(time=[midpoint])
            output_name = output_names.get(name, name)
            value.attrs = dict(selected[name].attrs)
            value.attrs["cell_methods"] = f"time: {method}"
            if method == "integral":
                value.attrs["cell_methods"] = "time: sum (interval: 1 hour)"
                if name == "RAINRATE":
                    value.attrs["standard_name"] = "precipitation_amount"
                    value.attrs["long_name"] = "24-hour precipitation amount"
            if name in output_units:
                value.attrs["units"] = output_units[name]
            variables[output_name] = value

        result = xr.Dataset(variables)
        result["time_bounds"] = xr.DataArray(
            np.asarray([[start, end]], dtype="datetime64[ns]"), dims=("time", "bounds")
        )
        result.time.attrs["bounds"] = "time_bounds"
        time_units = "minutes since 1970-01-01 00:00:00 UTC"
        result.time.encoding["units"] = time_units
        result["time_bounds"].encoding["units"] = time_units
        result.attrs.update(
            temporal_resolution="P1D",
            day_definition="model_interval",
            interval_start_utc=str(start),
            interval_end_utc=str(end),
            sample_endpoint_start_utc=str(expected[0]),
            sample_endpoint_end_utc=str(expected[-1]),
            aggregation_sample_count=24,
            aggregation_sample_interval="PT1H",
            source_files="\n".join(str(path.resolve()) for path in paths),
        )
        # The source collections are closed below. Materialize the comparatively small
        # one-record result first so its Dask graph never references closed NetCDF handles.
        return result.compute()
    finally:
        for dataset in opened:
            dataset.close()
