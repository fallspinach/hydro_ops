"""Read NOAA USCRN Hourly02 station observations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

MISSING_BELOW = -9000.0


@dataclass(frozen=True)
class HourlyObservations:
    station_id: str
    time: NDArray[np.datetime64]
    longitude: float
    latitude: float
    temperature_k: NDArray[np.float64]
    relative_humidity: NDArray[np.float64]
    shortwave_w_m2: NDArray[np.float64]


def _valid(values: NDArray[np.float64], flags: NDArray[np.float64] | None = None):
    result = np.where(values > MISSING_BELOW, values, np.nan)
    if flags is not None:
        result = np.where(flags == 0, result, np.nan)
    return result.astype(np.float64)


def read_hourly02(path: Path) -> HourlyObservations:
    """Read one fixed-width/whitespace Hourly02 file and apply published QC flags."""
    fields = np.loadtxt(path, dtype=str)
    if fields.ndim != 2 or fields.shape[1] != 38:
        raise ValueError(f"Expected 38 USCRN Hourly02 columns in {path}")
    timestamps = np.array(
        [
            np.datetime64(
                f"{day[:4]}-{day[4:6]}-{day[6:8]}T"
                f"{hour.zfill(4)[:2]}:{hour.zfill(4)[2:]}",
                "m",
            )
            for day, hour in fields[:, 1:3]
        ]
    )
    temperature = _valid(fields[:, 9].astype(float)) + 273.15
    shortwave = _valid(fields[:, 13].astype(float), fields[:, 14].astype(float))
    humidity = _valid(fields[:, 26].astype(float), fields[:, 27].astype(float)) / 100.0
    return HourlyObservations(
        station_id=str(fields[0, 0]),
        time=timestamps,
        longitude=float(fields[0, 6]),
        latitude=float(fields[0, 7]),
        temperature_k=temperature,
        relative_humidity=humidity,
        shortwave_w_m2=shortwave,
    )
