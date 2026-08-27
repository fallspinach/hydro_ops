"""Structural and physical validation for published daily NWM forcing streams."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from netCDF4 import Dataset, num2date

REQUIRED_FIELDS = ("T2D", "Q2D", "PSFC", "U2D", "V2D", "SWDOWN", "LWDOWN", "RAINRATE")
EXPECTED_GRID = (3840, 4608)
PHYSICAL_LIMITS = {
    "T2D": (180.0, 340.0),
    "Q2D": (0.0, 0.05),
    "PSFC": (20_000.0, 120_000.0),
    "U2D": (-150.0, 150.0),
    "V2D": (-150.0, 150.0),
    "SWDOWN": (0.0, 1_400.0),
    "LWDOWN": (0.0, 1_000.0),
    "RAINRATE": (0.0, 1.0),
}


def _times(dataset: Dataset) -> list[datetime]:
    variable = dataset["time"]
    calendar = variable.getncattr("calendar") if "calendar" in variable.ncattrs() else "standard"
    values = num2date(
        variable[:],
        variable.getncattr("units"),
        calendar=calendar,
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [value.replace(tzinfo=None) for value in np.asarray(values).reshape(-1)]


def validate_daily_forcing(
    path: Path,
    *,
    expected_revision: str | None = None,
    expected_grid: tuple[int, int] = EXPECTED_GRID,
) -> dict[str, Any]:
    """Fully scan a daily forcing file and return a JSON-serializable acceptance report."""
    path = Path(path)
    issues: list[str] = []
    metrics: dict[str, Any] = {}
    manifest = path.with_suffix(path.suffix + ".manifest.json")
    if not path.is_file() or path.stat().st_size == 0:
        return {"path": str(path), "accepted": False, "issues": ["file is missing or empty"]}
    if not manifest.is_file():
        issues.append("manifest is missing")
    abandoned = sorted(str(item) for item in path.parent.glob("*.part"))
    if abandoned:
        issues.append(f"abandoned partial files: {abandoned}")

    source_counts: Counter[int] = Counter()
    precip_counts: Counter[int] = Counter()
    with Dataset(path) as dataset:
        missing = sorted(set(REQUIRED_FIELDS) - set(dataset.variables))
        if missing:
            issues.append(f"missing required fields: {missing}")
        shape = (len(dataset.dimensions.get("y", ())), len(dataset.dimensions.get("x", ())))
        if shape != expected_grid:
            issues.append(f"grid is {shape}; expected {expected_grid}")
        try:
            times = _times(dataset)
        except (KeyError, AttributeError, ValueError) as error:
            times = []
            issues.append(f"invalid time coordinate: {error}")
        if len(times) != 24:
            issues.append(f"time record count is {len(times)}; expected 24")
        elif any((right - left).total_seconds() != 3600 for left, right in pairwise(times)):
            issues.append("time records are not strictly hourly")
        metrics["start_time"] = times[0].isoformat() if times else None
        metrics["end_time"] = times[-1].isoformat() if times else None

        revision = dataset.getncattr("prism_precipitation_revision") if (
            "prism_precipitation_revision" in dataset.ncattrs()
        ) else None
        metrics["revision"] = revision
        if expected_revision is not None and revision != expected_revision:
            issues.append(f"revision is {revision!r}; expected {expected_revision!r}")
        if "prism_reconciliation_accepted" in dataset.ncattrs():
            accepted = str(dataset.getncattr("prism_reconciliation_accepted")).lower()
            if accepted != "true":
                issues.append("PRISM precipitation reconciliation was not accepted")

        for name in REQUIRED_FIELDS:
            if name not in dataset.variables:
                continue
            variable = dataset[name]
            if variable.dimensions != ("time", "y", "x"):
                issues.append(f"{name} dimensions are {variable.dimensions}")
                continue
            low, high = PHYSICAL_LIMITS[name]
            finite_count = 0
            minimum = np.inf
            maximum = -np.inf
            for index in range(len(variable)):
                values = np.ma.asarray(variable[index])
                finite = np.asarray(values.compressed(), dtype=np.float64)
                if finite.size == 0:
                    continue
                finite_count += int(finite.size)
                minimum = min(minimum, float(np.min(finite)))
                maximum = max(maximum, float(np.max(finite)))
            metrics[name] = {
                "finite_count": finite_count,
                "minimum": None if finite_count == 0 else minimum,
                "maximum": None if finite_count == 0 else maximum,
            }
            if finite_count == 0:
                issues.append(f"{name} has no finite values")
            elif minimum < low or maximum > high:
                issues.append(f"{name} range [{minimum}, {maximum}] exceeds [{low}, {high}]")

        if "U2D" in dataset.variables and "V2D" in dataset.variables:
            for index in range(min(len(dataset["U2D"]), len(dataset["V2D"]))):
                u_mask = np.ma.getmaskarray(dataset["U2D"][index])
                v_mask = np.ma.getmaskarray(dataset["V2D"][index])
                if not np.array_equal(u_mask, v_mask):
                    issues.append(f"wind masks differ at time index {index}")
                    break
        for variable_name, counter in (
            ("forcing_source_id", source_counts),
            ("precip_source_id", precip_counts),
        ):
            if variable_name not in dataset.variables:
                issues.append(f"{variable_name} is missing")
                continue
            for index in range(len(dataset[variable_name])):
                values, counts = np.unique(dataset[variable_name][index], return_counts=True)
                counter.update({int(value): int(count) for value, count in zip(values, counts, strict=True)})
    metrics["forcing_source_counts"] = dict(sorted(source_counts.items()))
    metrics["precipitation_source_counts"] = dict(sorted(precip_counts.items()))
    if manifest.is_file():
        try:
            metrics["manifest"] = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError) as error:
            issues.append(f"manifest is invalid: {error}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "accepted": not issues,
        "issues": issues,
        "metrics": metrics,
    }


def validate_hourly_forcing_day(
    root: Path,
    day: date,
    *,
    expected_grid: tuple[int, int] = EXPECTED_GRID,
) -> dict[str, Any]:
    """Fully scan 24 separately published hourly LDASIN files for one UTC day."""
    root = Path(root)
    issues: list[str] = []
    extrema = {
        name: {"finite_count": 0, "minimum": np.inf, "maximum": -np.inf}
        for name in REQUIRED_FIELDS
    }
    source_counts: Counter[int] = Counter()
    precip_counts: Counter[int] = Counter()
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    paths: list[Path] = []
    total_bytes = 0
    for hour in range(24):
        valid = start + timedelta(hours=hour)
        path = root / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        paths.append(path)
        manifest = path.with_suffix(f"{path.suffix}.manifest.json")
        if not path.is_file() or path.stat().st_size == 0:
            issues.append(f"{valid:%Y%m%d%H}: file is missing or empty")
            continue
        total_bytes += path.stat().st_size
        if not manifest.is_file():
            issues.append(f"{valid:%Y%m%d%H}: manifest is missing")
        else:
            try:
                json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError) as error:
                issues.append(f"{valid:%Y%m%d%H}: manifest is invalid: {error}")
        try:
            with Dataset(path) as dataset:
                missing = sorted(set(REQUIRED_FIELDS) - set(dataset.variables))
                if missing:
                    issues.append(f"{valid:%Y%m%d%H}: missing required fields: {missing}")
                shape = (
                    len(dataset.dimensions.get("y", ())),
                    len(dataset.dimensions.get("x", ())),
                )
                if shape != expected_grid:
                    issues.append(f"{valid:%Y%m%d%H}: grid is {shape}; expected {expected_grid}")
                times = _times(dataset)
                expected = valid.replace(tzinfo=None)
                if times != [expected]:
                    issues.append(f"{valid:%Y%m%d%H}: time coordinate is {times!r}")
                if dataset.getncattr("valid_time") != expected.isoformat():
                    issues.append(f"{valid:%Y%m%d%H}: valid_time attribute is inconsistent")
                if dataset.getncattr("precipitation_status") != "present":
                    issues.append(f"{valid:%Y%m%d%H}: precipitation is not present")
                for name in REQUIRED_FIELDS:
                    if name not in dataset.variables:
                        continue
                    variable = dataset[name]
                    if variable.dimensions != ("time", "y", "x"):
                        issues.append(f"{valid:%Y%m%d%H}: {name} dimensions are {variable.dimensions}")
                        continue
                    values = np.asarray(np.ma.asarray(variable[0]).compressed(), dtype=np.float64)
                    if values.size:
                        extrema[name]["finite_count"] += int(values.size)
                        extrema[name]["minimum"] = min(extrema[name]["minimum"], float(values.min()))
                        extrema[name]["maximum"] = max(extrema[name]["maximum"], float(values.max()))
                if (
                    "U2D" in dataset.variables
                    and "V2D" in dataset.variables
                    and not np.array_equal(
                        np.ma.getmaskarray(dataset["U2D"][0]),
                        np.ma.getmaskarray(dataset["V2D"][0]),
                    )
                ):
                    issues.append(f"{valid:%Y%m%d%H}: wind masks differ")
                for variable_name, counter in (
                    ("forcing_source_id", source_counts),
                    ("precip_source_id", precip_counts),
                ):
                    if variable_name not in dataset.variables:
                        issues.append(f"{valid:%Y%m%d%H}: {variable_name} is missing")
                        continue
                    values, counts = np.unique(dataset[variable_name][0], return_counts=True)
                    counter.update(
                        {int(value): int(count) for value, count in zip(values, counts, strict=True)}
                    )
        except (OSError, AttributeError, ValueError, KeyError) as error:
            issues.append(f"{valid:%Y%m%d%H}: unreadable or invalid NetCDF: {error}")
    day_directory = root / day.strftime("%Y/%m/%d")
    abandoned = sorted(str(item) for item in day_directory.glob("*.daypart-*"))
    if abandoned:
        issues.append(f"abandoned daily staging files: {abandoned}")
    metrics: dict[str, Any] = {}
    for name, values in extrema.items():
        count = int(values["finite_count"])
        minimum = None if count == 0 else float(values["minimum"])
        maximum = None if count == 0 else float(values["maximum"])
        metrics[name] = {"finite_count": count, "minimum": minimum, "maximum": maximum}
        low, high = PHYSICAL_LIMITS[name]
        if count == 0:
            issues.append(f"{name} has no finite values")
        elif minimum < low or maximum > high:
            issues.append(f"{name} range [{minimum}, {maximum}] exceeds [{low}, {high}]")
    metrics["forcing_source_counts"] = dict(sorted(source_counts.items()))
    metrics["precipitation_source_counts"] = dict(sorted(precip_counts.items()))
    return {
        "root": str(root),
        "day": day.isoformat(),
        "paths": [str(path) for path in paths],
        "bytes": total_bytes,
        "accepted": not issues,
        "issues": issues,
        "metrics": metrics,
    }
