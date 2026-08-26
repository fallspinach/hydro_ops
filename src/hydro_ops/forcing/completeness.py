"""Fast filename-based completeness reports for forcing archives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.daily_archive import verified_daily_archive


@dataclass(frozen=True)
class DayCompleteness:
    product: str
    day: date
    expected: int
    present: int
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.present == self.expected and not self.missing


def iter_days(start: date, end: date):
    if start > end:
        raise ValueError("Start date is after end date")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _hourly_report(product: str, day: date, found: set[int]) -> DayCompleteness:
    missing = tuple(f"{day:%Y-%m-%d}T{hour:02d}:00Z" for hour in range(24) if hour not in found)
    return DayCompleteness(product, day, 24, len(found), missing)


def nldas2_day(root: Path, day: date) -> DayCompleteness:
    daily = root / day.strftime("%Y") / f"NLDAS_FORA0125_H.A{day:%Y%m%d}.020.nc"
    if verified_daily_archive(daily, day):
        return DayCompleteness("nldas2", day, 24, 24, ())
    directory = root / day.strftime("%Y/%j")
    prefix = f"NLDAS_FORA0125_H.A{day:%Y%m%d}."
    found: set[int] = set()
    for path in directory.glob(f"{prefix}*.nc*"):
        fields = path.name.split(".")
        if len(fields) >= 3 and len(fields[2]) == 4 and fields[2].isdigit():
            hour, minute = int(fields[2][:2]), int(fields[2][2:])
            if minute == 0 and 0 <= hour <= 23 and ".part" not in path.name:
                found.add(hour)
    return _hourly_report("nldas2", day, found)


def hrrr_day(root: Path, day: date) -> DayCompleteness:
    daily = root / day.strftime("%Y/%m") / f"hrrr_forcing.{day:%Y%m%d}.nc"
    if verified_daily_archive(daily, day):
        return DayCompleteness("hrrr", day, 24, 24, ())
    directory = root / day.strftime("%Y/%m/%d")
    prefix = f"hrrr_forcing.{day:%Y%m%d}"
    found: set[int] = set()
    for path in directory.glob(f"{prefix}??.grib2.nc"):
        stamp = path.name.removeprefix("hrrr_forcing.")[:10]
        try:
            valid = datetime.strptime(stamp, "%Y%m%d%H").replace(tzinfo=UTC)
        except ValueError:
            continue
        if valid.date() == day:
            found.add(valid.hour)
    return _hourly_report("hrrr", day, found)


def prism_day(root: Path, day: date, variable: str) -> DayCompleteness:
    path = root / variable / day.strftime("%Y/%m") / f"prism_{variable}_us_25m_{day:%Y%m%d}.nc"
    present = int(path.is_file() and path.stat().st_size > 0)
    missing = () if present else (day.isoformat(),)
    return DayCompleteness(f"prism_{variable}", day, 1, present, missing)


def report_range(
    product: str,
    root: Path,
    start: date,
    end: date,
) -> list[DayCompleteness]:
    """Report expected timestamps without opening thousands of completed files."""
    reports = []
    for day in iter_days(start, end):
        if product == "nldas2":
            reports.append(nldas2_day(root, day))
        elif product == "hrrr":
            reports.append(hrrr_day(root, day))
        elif product.startswith("prism_"):
            reports.append(prism_day(root, day, product.removeprefix("prism_")))
        else:
            raise ValueError(f"Unknown product {product!r}")
    return reports
