"""Grid-window planning for reproducible NWM domain subsets."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GridWindow:
    west_east_start: int
    west_east_end: int
    south_north_start: int
    south_north_end: int

    @property
    def shape(self) -> tuple[int, int]:
        return (
            self.south_north_end - self.south_north_start + 1,
            self.west_east_end - self.west_east_start + 1,
        )

    def refined(self, factor: int) -> GridWindow:
        if factor < 1:
            raise ValueError("refinement factor must be positive")
        return GridWindow(
            self.west_east_start * factor,
            (self.west_east_end + 1) * factor - 1,
            self.south_north_start * factor,
            (self.south_north_end + 1) * factor - 1,
        )


def window_from_bbox(
    latitude: np.ndarray,
    longitude: np.ndarray,
    bbox: tuple[float, float, float, float],
    padding: int = 0,
) -> GridWindow:
    """Return the smallest rectangular grid window intersecting a lon/lat bbox."""
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("bbox must be WEST SOUTH EAST NORTH")
    if latitude.shape != longitude.shape or latitude.ndim != 2:
        raise ValueError("latitude and longitude must be matching 2-D arrays")
    selected = (
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & (longitude >= west)
        & (longitude <= east)
        & (latitude >= south)
        & (latitude <= north)
    )
    rows, columns = np.where(selected)
    if not rows.size:
        raise ValueError("bbox does not intersect the NWM grid")
    ny, nx = latitude.shape
    return GridWindow(
        max(0, int(columns.min()) - padding),
        min(nx - 1, int(columns.max()) + padding),
        max(0, int(rows.min()) - padding),
        min(ny - 1, int(rows.max()) + padding),
    )
