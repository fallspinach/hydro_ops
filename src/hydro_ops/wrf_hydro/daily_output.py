"""Reference reductions for daily-resolution WRF-Hydro output validation."""

from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import xarray as xr

REDUCERS = {"mean", "sum", "minimum", "maximum", "first", "last", "omit"}


def load_reducers(path: Path, product: str) -> dict[str, str]:
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    try:
        reducers = dict(document[product])
    except KeyError as error:
        raise ValueError(f"No reducer table for {product}") from error
    unknown = set(reducers.values()) - REDUCERS
    if unknown:
        raise ValueError(f"Unknown daily reducers: {sorted(unknown)}")
    return reducers


def reduce_samples(values: np.ndarray, method: str) -> np.ndarray:
    """Reduce a leading sample dimension without hiding missing samples."""
    if method == "mean":
        return np.mean(values, axis=0)
    if method == "sum":
        return np.sum(values, axis=0)
    if method == "minimum":
        return np.min(values, axis=0)
    if method == "maximum":
        return np.max(values, axis=0)
    if method == "first":
        return values[0]
    if method == "last":
        return values[-1]
    if method == "omit":
        raise ValueError("omit variables must not be passed to reduce_samples")
    raise ValueError(f"Unknown daily reducer: {method}")


def reduce_hourly_files(
    paths: list[Path], product: str, reducers: dict[str, str]
) -> xr.Dataset:
    """Create an in-memory daily-resolution oracle from ordered hourly files."""
    if not paths:
        raise ValueError("At least one hourly file is required")
    datasets = [xr.open_dataset(path, mask_and_scale=True) for path in paths]
    try:
        result = datasets[0].copy(deep=True)
        sample_times = np.asarray(
            [np.asarray(dataset["time"].values).reshape(-1)[0] for dataset in datasets]
        )
        if len(sample_times) < 2:
            raise ValueError("At least two hourly samples are required to infer time bounds")
        intervals = np.diff(sample_times)
        if not np.all(intervals == intervals[0]):
            raise ValueError("Hourly output times are not regularly spaced")
        period_start = sample_times[0] - intervals[0]
        period_end = sample_times[-1]
        midpoint = period_start + (period_end - period_start) / 2
        result = result.assign_coords(time=("time", [midpoint]))
        result["time_bounds"] = xr.DataArray(
            np.asarray([[period_start, period_end]]), dims=("time", "bounds")
        )
        result["time"].attrs["bounds"] = "time_bounds"
        time_units = "minutes since 1970-01-01 00:00:00 UTC"
        result["time"].encoding["units"] = time_units
        result["time_bounds"].encoding["units"] = time_units
        result.attrs["temporal_resolution"] = "P1D"
        result.attrs["aggregation_convention"] = "per-variable cell_methods"
        result.attrs["aggregation_sample_count"] = len(paths)
        result.attrs["aggregation_sample_interval"] = str(intervals[0])
        for name, method in reducers.items():
            if name not in result:
                raise ValueError(f"Configured {product} variable is absent: {name}")
            if method == "omit":
                result = result.drop_vars(name)
                continue
            samples = []
            for dataset in datasets:
                value = dataset[name]
                if "time" in value.dims:
                    if value.sizes["time"] != 1:
                        raise ValueError(f"Hourly {name} has more than one time record")
                    value = value.isel(time=0, drop=True)
                samples.append(np.asarray(value.values, dtype=np.float64))
            reduced = reduce_samples(np.stack(samples), method)
            original = result[name]
            if "time" in original.dims:
                reduced = np.expand_dims(reduced, original.get_axis_num("time"))
            attrs = dict(original.attrs)
            attrs["cell_methods"] = f"time: {method}"
            result[name] = xr.DataArray(reduced, dims=original.dims, attrs=attrs)
        return result
    finally:
        for dataset in datasets:
            dataset.close()
