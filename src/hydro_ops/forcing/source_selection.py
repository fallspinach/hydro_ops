"""Select a structurally valid whole-hour forcing source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.inventory import inspect_forcing_file


@dataclass(frozen=True)
class SelectedSource:
    product: str
    path: Path
    valid_time: datetime
    fallback_used: bool
    rejected: tuple[str, ...]


def source_path(product: str, root: Path, valid_time: datetime) -> Path:
    """Return the canonical archive path for one valid hour."""
    valid_time = valid_time.astimezone(UTC)
    if product == "nldas2":
        return root / valid_time.strftime(
            "%Y/%j/NLDAS_FORA0125_H.A%Y%m%d.%H00.020.nc"
        )
    if product == "hrrr":
        return root / valid_time.strftime(
            "%Y/%m/%d/hrrr_forcing.%Y%m%d%H.grib2.nc"
        )
    raise ValueError(f"Unknown hourly source product {product!r}")


def source_paths(product: str, root: Path, valid_time: datetime) -> tuple[Path, ...]:
    """Return eligible hourly and daily paths, in preference order."""
    hourly = source_path(product, root, valid_time)
    if product == "nldas2":
        daily = root / valid_time.strftime("%Y/NLDAS_FORA0125_H.A%Y%m%d.020.nc")
        return hourly, daily
    if product == "hrrr":
        daily = root / valid_time.strftime("%Y/%m/hrrr_forcing.%Y%m%d.nc")
        return hourly, daily
    return (hourly,)


def _contains_time(path: Path, valid_time: datetime) -> bool:
    with Dataset(path) as dataset:
        if "time" not in dataset.variables:
            return False
        variable = dataset["time"]
        units = getattr(variable, "units", None)
        if not units:
            return False
        calendar = getattr(variable, "calendar", "standard")
        values = num2date(
            variable[:], units, calendar=calendar, only_use_cftime_datetimes=False
        )
        expected = valid_time.replace(tzinfo=None)
        return any(value.replace(tzinfo=None) == expected for value in values)


def select_hourly_source(
    valid_time: datetime,
    nldas2_root: Path,
    hrrr_root: Path,
    *,
    preference: tuple[str, ...] = ("nldas2", "hrrr"),
) -> SelectedSource:
    """Select the first present, valid, exact-time whole-hour source."""
    valid_time = valid_time.astimezone(UTC)
    roots = {"nldas2": nldas2_root, "hrrr": hrrr_root}
    rejected: list[str] = []
    for index, product in enumerate(preference):
        if product not in roots:
            raise ValueError(f"Unsupported source preference {product!r}")
        paths = source_paths(product, roots[product], valid_time)
        path = next((candidate for candidate in paths if candidate.is_file()), None)
        if path is None:
            rejected.append(f"{product}: missing {' or '.join(map(str, paths))}")
            continue
        try:
            inventory = inspect_forcing_file(path, product)
        except OSError as error:
            rejected.append(f"{product}: unreadable {path}: {error}")
            continue
        if not inventory.valid:
            rejected.append(f"{product}: invalid {path}: {'; '.join(inventory.issues)}")
            continue
        expected = valid_time.replace(tzinfo=None).isoformat()
        contains_time = (
            inventory.valid_time == expected
            if inventory.valid_time is not None
            else _contains_time(path, valid_time)
        )
        if not contains_time:
            rejected.append(
                f"{product}: does not contain time {valid_time.isoformat()}: {path}"
            )
            continue
        return SelectedSource(product, path, valid_time, index > 0, tuple(rejected))
    raise FileNotFoundError(
        f"No valid forcing source for {valid_time.isoformat()}: {' | '.join(rejected)}"
    )
