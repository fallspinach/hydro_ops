"""Opt-in sparse GFS fallback outside native HRRR support, within the common envelope."""

from __future__ import annotations

import hashlib
import os

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.thermodynamics import finalize_target_state, prepare_reference_state

MET_FIELDS = ("T2D", "Q2D", "PSFC", "LWDOWN", "SWDOWN", "U2D", "V2D")


def sha(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def lambert_coordinates(latitude, longitude):
    """Dimensionless spherical HRRR Lambert coordinates; scale cancels in grid indices."""
    n = np.sin(np.deg2rad(38.5))
    radius = np.tan(np.pi / 4 + np.deg2rad(latitude) / 2) ** -n
    theta = n * np.deg2rad((longitude + 97.5 + 180) % 360 - 180)
    return radius * np.sin(theta), -radius * np.cos(theta)


def bilinear_weights(lat, lon, target_lat, target_lon):
    if np.any(np.diff(lat) <= 0) or np.any(np.diff(lon) <= 0):
        raise ValueError("Source coordinates must increase strictly")
    iy = np.searchsorted(lat, target_lat, side="right") - 1
    ix = np.searchsorted(lon, target_lon, side="right") - 1
    if np.any((iy < 0) | (ix < 0) | (iy >= len(lat) - 1) | (ix >= len(lon) - 1)):
        raise ValueError("No extrapolation: target outside buffered GFS grid")
    fy = (target_lat - lat[iy]) / (lat[iy + 1] - lat[iy])
    fx = (target_lon - lon[ix]) / (lon[ix + 1] - lon[ix])
    addresses = np.array([iy * len(lon) + ix, iy * len(lon) + ix + 1,
                          (iy + 1) * len(lon) + ix, (iy + 1) * len(lon) + ix + 1]).T
    weights = np.array([(1-fy)*(1-fx), (1-fy)*fx, fy*(1-fx), fy*fx]).T
    return addresses, weights


def build_geometry(envelope, hrrr_grid, target_elevation, source_lat, source_lon, destination, *, model_terrain=None):
    if destination.exists():
        raise FileExistsError(destination)
    with Dataset(hrrr_grid) as source:
        sx, sy = lambert_coordinates(np.asarray(source["latitude"][:]), np.asarray(source["longitude"][:]))
    ny, nx = sx.shape
    x0, y0 = sx[0, 0], sy[0, 0]
    dx, dy = (sx[0, -1] - x0) / (nx - 1), (sy[-1, 0] - y0) / (ny - 1)
    # Verify the projection against the full decoded HRRR grid, rather than
    # relying on an approximate geographic bounding box or filled target values.
    if max(np.max(np.abs((sx-x0)/dx-np.arange(nx))), np.max(np.abs((sy-y0)/dy-np.arange(ny)[:, None]))) > 0.01:
        raise ValueError("HRRR projection/grid does not match verified geometry")
    del sx, sy
    with Dataset(envelope) as grid:
        keep, active = np.asarray(grid["keep"][:], bool), np.asarray(grid["active"][:], bool)
        lat, lon = np.asarray(grid["lat"][:]), np.asarray(grid["lon"][:])
        if sha(keep) != grid.keep_sha256 or np.any(active & ~keep):
            raise ValueError("Invalid common envelope")
    x, y = lambert_coordinates(lat, lon)
    ix, iy = (x - x0) / dx, (y - y0) / dy
    supported = (ix >= 0) & (ix <= nx-1) & (iy >= 0) & (iy <= ny-1)
    indices = np.flatnonzero(keep & ~supported)
    with Dataset(target_elevation) as terrain:
        elevation = np.ma.filled(terrain["elevation"][:], np.nan).ravel()[indices]
    absent_elevation = ~np.isfinite(elevation)
    terrain_fallback = np.zeros(indices.shape, dtype=bool)
    if model_terrain is not None and np.any(absent_elevation & active.ravel()[indices]):
        with Dataset(model_terrain) as model:
            np.testing.assert_allclose(np.asarray(model["XLAT"][0]), lat, atol=1e-5, rtol=0)
            np.testing.assert_allclose(np.asarray(model["XLONG"][0]), lon, atol=1e-5, rtol=0)
            model_height = np.ma.filled(model["HGT"][0], np.nan).ravel()[indices]
        terrain_fallback = absent_elevation & active.ravel()[indices] & np.isfinite(model_height) & (model_height > -500) & (model_height < 9000)
        elevation[terrain_fallback] = model_height[terrain_fallback]
        absent_elevation = ~np.isfinite(elevation)
    if np.any(absent_elevation & active.ravel()[indices]):
        raise ValueError("Active target gap elevation is incomplete")
    skipped_inactive = int(absent_elevation.sum())
    terrain_fallback_count = int(terrain_fallback.sum())
    terrain_fallback = terrain_fallback[~absent_elevation]
    indices, elevation = indices[~absent_elevation], elevation[~absent_elevation]
    addresses, weights = bilinear_weights(source_lat, source_lon, lat.ravel()[indices], lon.ravel()[indices])
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(".part.npz")
    np.savez_compressed(part, indices=indices, addresses=addresses, weights=weights,
        latitude=lat.ravel()[indices], longitude=lon.ravel()[indices], elevation=elevation,
        active=active.ravel()[indices], shape=np.array(keep.shape),
        terrain_fallback=terrain_fallback,
        source_lat=source_lat, source_lon=source_lon, envelope_sha256=np.array(sha(keep)),
        target_lat_sha256=np.array(sha(lat)), target_lon_sha256=np.array(sha(lon)))
    os.replace(part, destination)
    return {"gap_cells": len(indices), "active_gap_cells": int(active.ravel()[indices].sum()),
            "inactive_missing_elevation_excluded": skipped_inactive,
            "active_elevation_from_model_HGT": terrain_fallback_count,
            "latitude_range": [float(lat.ravel()[indices].min()), float(lat.ravel()[indices].max())]}


def remap_hour(source, geometry, *, precipitation_remapper=None, return_quality=False):
    np.testing.assert_array_equal(source.lat.values, geometry["source_lat"])
    np.testing.assert_array_equal(source.lon.values, geometry["source_lon"])
    def remap(values):
        return (np.asarray(values).ravel()[geometry["addresses"]] * geometry["weights"]).sum(axis=1)

    state = prepare_reference_state(source.T2D, source.PSFC, source.Q2D, source.LWDOWN,
                                    source.elevation, relative_humidity_tolerance=0.2)
    target = finalize_target_state(remap(state.temperature), remap(state.pressure),
        remap(state.relative_humidity), remap(state.longwave_factor), geometry["elevation"],
        relative_humidity_tolerance=0.2)
    values = {"T2D": target.temperature, "PSFC": target.pressure,
              "Q2D": target.specific_humidity, "LWDOWN": target.downward_longwave,
              "SWDOWN": remap(source.SWDOWN), "U2D": remap(source.U2D), "V2D": remap(source.V2D),
              "RAINRATE": (remap(source.precipitation_depth) if precipitation_remapper is None
                           else precipitation_remapper(source.precipitation_depth)) / 3600.0}
    for name, array in values.items():
        if not np.isfinite(array).all():
            raise ValueError(f"Incomplete GFS fallback {name}")
        values[name] = array.astype(np.float32)
    if return_quality:
        clipped = ((state.qc_flags.ravel()[geometry["addresses"]] & 3) != 0).any(axis=1)
        clipped |= (target.qc_flags & 3) != 0
        return values, clipped
    return values


def merge_gap(primary, fallback, indices, precipitation_supported, *, nldas_available):
    """Explicit adapter: caller supplies genuine precipitation support, not finiteness.

    Geometric indices must come from the validated cache. Return separate usage
    masks for provenance; do not reinterpret existing model/source-ID registries.
    """
    output = {name: np.asarray(values).copy() for name, values in primary.items()}
    used = np.zeros(primary["T2D"].shape, dtype=bool)
    rain_used = used.copy()
    if nldas_available:
        return output, used, rain_used
    shape = primary["T2D"].shape
    if (np.asarray(precipitation_supported).shape != shape
            or any(primary[name].shape != shape for name in (*MET_FIELDS, "RAINRATE"))
            or indices.ndim != 1 or len(np.unique(indices)) != len(indices)
            or np.any(indices < 0) or np.any(indices >= np.prod(shape))):
        raise ValueError("Invalid gap indices, field shape, or explicit precipitation support")
    for name in MET_FIELDS:
        if fallback[name].shape != indices.shape or not np.isfinite(fallback[name]).all():
            raise ValueError(f"Invalid fallback {name}")
        output[name].ravel()[indices] = fallback[name]
    used.ravel()[indices] = True
    rain_indices = ~np.asarray(precipitation_supported, bool).ravel()[indices]
    if fallback["RAINRATE"].shape != indices.shape or not np.isfinite(fallback["RAINRATE"]).all() or np.any(fallback["RAINRATE"] < 0):
        raise ValueError("Invalid fallback precipitation")
    output["RAINRATE"].ravel()[indices[rain_indices]] = fallback["RAINRATE"][rain_indices]
    rain_used.ravel()[indices[rain_indices]] = True
    return output, used, rain_used
