"""Report the newest locally available forcing products."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.config import Settings


@dataclass(frozen=True)
class Coverage:
    product: str
    latest: datetime | None
    files: int


def _latest(root: Path, pattern: str, expression: str, timestamp_format: str) -> Coverage:
    regex = re.compile(expression)
    latest: datetime | None = None
    count = 0
    for path in root.glob(pattern):
        match = regex.search(path.name)
        if not match:
            continue
        count += 1
        value = datetime.strptime("".join(match.groups()), timestamp_format).replace(tzinfo=UTC)
        latest = value if latest is None or value > latest else latest
    return Coverage("", latest, count)


def forcing_coverage(settings: Settings) -> list[Coverage]:
    """Return latest valid times and file counts for every maintained stream."""
    specifications = [
        (
            "NLDAS-2",
            settings.nldas_data_dir,
            "*/*/NLDAS_FORA0125_H.A*.nc*",
            r"\.A(\d{8})\.(\d{4})\.",
            "%Y%m%d%H%M",
        ),
        (
            "Stage-IV realtime",
            settings.stage4_data_dir / "netcdf/realtime",
            "*/*/*/st4_conus.*.grb2.nc",
            r"st4_conus\.(\d{10})\.",
            "%Y%m%d%H",
        ),
        (
            "Stage-IV archive",
            settings.stage4_data_dir / "netcdf/archive",
            "*/*/*/st4_conus.*.grb2.nc",
            r"st4_conus\.(\d{10})\.",
            "%Y%m%d%H",
        ),
    ]
    for variable in settings.prism_variables:
        specifications.append(
            (
                f"PRISM {variable}",
                settings.prism_data_dir / variable,
                "*/*/*.nc",
                rf"prism_{re.escape(variable)}_us_25m_(\d{{8}})\.nc$",
                "%Y%m%d",
            )
        )
    specifications.extend(
        [
            (
                "HRRR forcing",
                settings.hrrr_data_dir,
                "*/*/*/hrrr_forcing.*.grib2.nc",
                r"hrrr_forcing\.(\d{10})\.grib2\.nc$",
                "%Y%m%d%H",
            ),
            *[
                (
                    f"MRMS {product}",
                    settings.mrms_data_dir / "netcdf" / product,
                    "*/*/*/*.grib2.nc",
                    r"_(\d{8})-(\d{6})\.grib2\.nc$",
                    "%Y%m%d%H%M%S",
                )
                for product in settings.mrms_products
            ],
        ]
    )
    coverage = []
    for product, root, pattern, expression, timestamp_format in specifications:
        found = _latest(root, pattern, expression, timestamp_format)
        coverage.append(Coverage(product, found.latest, found.files))
    return coverage


def format_coverage(rows: list[Coverage], *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    lines = [f"{'Product':<24} {'Latest valid UTC':<18} {'Age':>10} {'Files':>9}"]
    lines.append("-" * 65)
    for row in rows:
        if row.latest is None:
            stamp, age = "missing", "-"
        else:
            stamp = row.latest.strftime("%Y-%m-%d %H:%M")
            hours = max(0.0, (now - row.latest).total_seconds() / 3600)
            age = f"{hours:.1f} h" if hours < 72 else f"{hours / 24:.1f} d"
        lines.append(f"{row.product:<24} {stamp:<18} {age:>10} {row.files:>9,d}")
    return "\n".join(lines)
