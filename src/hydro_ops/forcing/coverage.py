"""Coverage checks and conservative edge filling for NWM forcing grids."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_edt


@dataclass(frozen=True)
class FillResult:
    values: np.ndarray
    repaired: np.ndarray
    distance: np.ndarray


@dataclass(frozen=True)
class GeographicFillResult:
    values: np.ndarray
    repaired: np.ndarray
    distance: np.ndarray


def geographic_domain_mask(
    latitude: np.ndarray,
    longitude: np.ndarray,
    *,
    south: float = 25.0,
    north: float = 53.0,
    west: float = -125.0,
    east: float = -67.0,
) -> np.ndarray:
    """Return the inclusive NLDAS-2 geographic forcing domain.

    Longitudes in either ``[-180, 180]`` or ``[0, 360]`` convention are accepted.
    """
    if latitude.shape != longitude.shape:
        raise ValueError("latitude and longitude must have identical shapes")
    normalized_longitude = np.where(longitude > 180.0, longitude - 360.0, longitude)
    return (
        np.isfinite(latitude)
        & np.isfinite(normalized_longitude)
        & (latitude >= south)
        & (latitude <= north)
        & (normalized_longitude >= west)
        & (normalized_longitude <= east)
    )


def fill_geographic_domain_gaps(
    values: np.ndarray,
    *,
    missing: np.ndarray,
    domain: np.ndarray,
) -> GeographicFillResult:
    """Fill every missing target inside a hard domain from its nearest valid donor.

    Both targets and donors are restricted to ``domain``. Values outside it are never
    changed and can never be selected as donors. Distance is Euclidean grid-cell distance.
    """
    if values.shape != missing.shape or values.shape != domain.shape:
        raise ValueError("values, missing, and domain must have identical shapes")
    targets = missing & domain
    donors = ~missing & domain
    if np.any(targets) and not np.any(donors):
        raise ValueError("geographic domain contains missing targets but no valid donor")
    output = values.copy()
    distances = np.zeros(values.shape, dtype=np.float32)
    if not np.any(targets):
        return GeographicFillResult(output, targets, distances)
    distance, indices = distance_transform_edt(~donors, return_indices=True)
    output[targets] = values[indices[0][targets], indices[1][targets]]
    distances[targets] = distance[targets]
    return GeographicFillResult(output, targets, distances)


def persistent_gap_mask(missing: np.ndarray) -> np.ndarray:
    """Return cells missing at every time in at least one forcing variable.

    ``missing`` has shape ``(variable, time, y, x)``. Persistent gaps describe a
    stable source/target coverage mismatch. They are intentionally distinct from
    transient missing data, which must never be silently filled.
    """
    if missing.ndim != 4:
        raise ValueError("missing must have dimensions (variable, time, y, x)")
    return np.any(np.all(missing, axis=1), axis=0)


def fill_persistent_gaps(
    values: np.ndarray,
    *,
    missing: np.ndarray,
    active_land: np.ndarray,
    allowed: np.ndarray,
    max_distance: int,
) -> FillResult:
    """Fill approved active-land gaps from the nearest four-neighbor valid cell.

    Propagation may cross missing non-land fringe cells, but only approved active
    land targets are changed in the returned array. Distance is measured in grid
    cells with Manhattan connectivity.
    """
    if values.shape != missing.shape or values.shape != active_land.shape:
        raise ValueError("values, missing, and active_land must have identical shapes")
    if allowed.shape != values.shape:
        raise ValueError("allowed must have the same shape as values")
    targets = missing & active_land
    disallowed = targets & ~allowed
    if np.any(disallowed):
        raise ValueError(f"found {int(disallowed.sum())} transient or unapproved land gaps")
    repaired = targets.copy()
    distances = np.zeros(values.shape, dtype=np.int16)
    if not np.any(targets):
        return FillResult(values.copy(), repaired, distances)

    work = values.copy()
    unresolved = missing.copy()
    valid = ~unresolved
    frontier = valid.copy()
    step = 0
    while np.any(unresolved & targets):
        step += 1
        if step > max_distance:
            count = int(np.count_nonzero(unresolved & targets))
            raise ValueError(f"{count} approved land gaps exceed {max_distance} grid cells")
        neighbors = np.zeros_like(valid)
        neighbors[1:] |= frontier[:-1]
        neighbors[:-1] |= frontier[1:]
        neighbors[:, 1:] |= frontier[:, :-1]
        neighbors[:, :-1] |= frontier[:, 1:]
        new = neighbors & unresolved
        if not np.any(new):
            raise ValueError("approved land gaps cannot be reached from valid forcing")
        for destination, source in (
            ((slice(1, None), slice(None)), (slice(None, -1), slice(None))),
            ((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
            ((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
            ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
        ):
            take = new[destination] & valid[source]
            destination_view = work[destination]
            destination_view[take] = work[source][take]
        distances[new] = step
        valid |= new
        unresolved &= ~new
        frontier = new

    output = values.copy()
    output[targets] = work[targets]
    distances[~targets] = 0
    return FillResult(output, repaired, distances)
