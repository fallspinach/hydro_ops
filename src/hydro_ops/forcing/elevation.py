"""Derive target-grid elevation from a geographic raster without full-grid loading."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from osgeo import gdal

gdal.UseExceptions()


def _bilinear_window(
    band,
    longitude: np.ndarray,
    latitude: np.ndarray,
    transform: tuple[float, float, float, float, float, float],
    nodata: float | None,
) -> np.ma.MaskedArray:
    origin_x, pixel_x, rotation_x, origin_y, rotation_y, pixel_y = transform
    if rotation_x != 0 or rotation_y != 0 or pixel_x <= 0 or pixel_y >= 0:
        raise ValueError("DEM must be an unrotated north-up geographic raster")
    column = (longitude - origin_x) / pixel_x - 0.5
    row = (latitude - origin_y) / pixel_y - 0.5
    column0 = np.floor(column).astype(np.int64)
    row0 = np.floor(row).astype(np.int64)
    outside = (
        (column0 < 0)
        | (row0 < 0)
        | (column0 + 1 >= band.XSize)
        | (row0 + 1 >= band.YSize)
        | ~np.isfinite(longitude)
        | ~np.isfinite(latitude)
    )
    safe_column = np.clip(column0, 0, band.XSize - 2)
    safe_row = np.clip(row0, 0, band.YSize - 2)
    x_min, x_max = int(safe_column.min()), int(safe_column.max()) + 1
    y_min, y_max = int(safe_row.min()), int(safe_row.max()) + 1
    raster = band.ReadAsArray(x_min, y_min, x_max - x_min + 1, y_max - y_min + 1)
    local_x = safe_column - x_min
    local_y = safe_row - y_min
    fraction_x = column - column0
    fraction_y = row - row0
    neighbors = np.stack(
        (
            raster[local_y, local_x],
            raster[local_y, local_x + 1],
            raster[local_y + 1, local_x],
            raster[local_y + 1, local_x + 1],
        )
    ).astype(np.float64)
    weights = np.stack(
        (
            (1 - fraction_x) * (1 - fraction_y),
            fraction_x * (1 - fraction_y),
            (1 - fraction_x) * fraction_y,
            fraction_x * fraction_y,
        )
    )
    invalid = ~np.isfinite(neighbors)
    if nodata is not None:
        invalid |= neighbors == nodata
    weights = np.where(invalid, 0.0, weights)
    weight_sum = weights.sum(axis=0)
    values = np.divide(
        (neighbors * weights).sum(axis=0),
        weight_sum,
        out=np.zeros_like(weight_sum),
        where=weight_sum > 0,
    )
    return np.ma.array(values, mask=outside | (weight_sum == 0))


def create_target_elevation(
    dem_path: Path,
    target_grid_path: Path,
    output_path: Path,
    *,
    force: bool = False,
    rows_per_chunk: int = 120,
) -> None:
    """Bilinearly sample a WGS84 DEM at NWM cell centers into a compact NetCDF file."""
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f"{output_path.name}.part")
    partial.unlink(missing_ok=True)
    dem = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem is None:
        raise RuntimeError(f"Could not open DEM: {dem_path}")
    if not dem.GetProjection() or "GEOGCS" not in dem.GetProjection():
        raise ValueError("DEM must use a geographic coordinate reference system")
    band = dem.GetRasterBand(1)
    try:
        with Dataset(target_grid_path) as target, Dataset(
            partial, "w", format="NETCDF4"
        ) as output:
            for name in ("lat", "lon", "active_domain"):
                if name not in target.variables:
                    raise RuntimeError(f"Target grid is missing {name}")
            ny, nx = target.dimensions["y"].size, target.dimensions["x"].size
            output.createDimension("y", ny)
            output.createDimension("x", nx)
            output.setncatts(
                {
                    "Conventions": "CF-1.8",
                    "title": "NWM CONUS 1-km target elevation",
                    "source_dem": str(dem_path),
                    "target_grid": str(target_grid_path),
                    "interpolation": "bilinear at target cell centers",
                    "history": f"{datetime.now(UTC).isoformat()} created by hydro_ops",
                }
            )
            y_coordinate = output.createVariable("y", "i4", ("y",))
            x_coordinate = output.createVariable("x", "i4", ("x",))
            y_coordinate[:] = np.arange(ny, dtype=np.int32)
            x_coordinate[:] = np.arange(nx, dtype=np.int32)
            y_coordinate.long_name = "NWM grid row index"
            x_coordinate.long_name = "NWM grid column index"
            elevation = output.createVariable(
                "elevation",
                "f4",
                ("y", "x"),
                fill_value=np.float32(-9999.0),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=(min(rows_per_chunk, ny), min(288, nx)),
            )
            elevation.setncatts(
                {
                    "standard_name": "surface_altitude",
                    "long_name": "NWM forcing-grid surface elevation sampled from DEM",
                    "units": "m",
                    "positive": "up",
                }
            )
            for start in range(0, ny, rows_per_chunk):
                stop = min(start + rows_per_chunk, ny)
                longitude = np.ma.filled(target["lon"][start:stop, :], np.nan)
                latitude = np.ma.filled(target["lat"][start:stop, :], np.nan)
                values = _bilinear_window(
                    band, longitude, latitude, dem.GetGeoTransform(), band.GetNoDataValue()
                )
                active = np.asarray(target["active_domain"][start:stop, :]) == 1
                values.mask = np.ma.getmaskarray(values) | ~active
                elevation[start:stop, :] = values.astype(np.float32)
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        dem = None


def create_regular_grid_elevation(
    dem_path: Path,
    grid_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> None:
    """Sample a geographic DEM onto a regular one-dimensional lat/lon source grid."""
    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f"{output_path.name}.part")
    partial.unlink(missing_ok=True)
    dem = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem is None:
        raise RuntimeError(f"Could not open DEM: {dem_path}")
    band = dem.GetRasterBand(1)
    try:
        with Dataset(grid_path) as grid:
            latitude_1d = np.asarray(grid["lat"][:], dtype=np.float64)
            longitude_1d = np.asarray(grid["lon"][:], dtype=np.float64)
        longitude, latitude = np.meshgrid(longitude_1d, latitude_1d)
        values = _bilinear_window(
            band, longitude, latitude, dem.GetGeoTransform(), band.GetNoDataValue()
        )
        with Dataset(partial, "w", format="NETCDF4") as output:
            output.createDimension("lat", latitude_1d.size)
            output.createDimension("lon", longitude_1d.size)
            output.setncatts(
                {
                    "Conventions": "CF-1.8",
                    "title": "Source-grid elevation sampled from GMTED2010",
                    "source_dem": str(dem_path),
                    "source_grid": str(grid_path),
                    "history": f"{datetime.now(UTC).isoformat()} created by hydro_ops",
                }
            )
            output.createVariable("lat", "f8", ("lat",))[:] = latitude_1d
            output.createVariable("lon", "f8", ("lon",))[:] = longitude_1d
            elevation = output.createVariable(
                "elevation", "f4", ("lat", "lon"), fill_value=np.float32(-9999),
                zlib=True, complevel=4, shuffle=True,
                chunksizes=(min(120, latitude_1d.size), min(288, longitude_1d.size)),
            )
            elevation.setncatts(
                {"standard_name": "surface_altitude", "units": "m", "positive": "up"}
            )
            elevation[:] = values.astype(np.float32)
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        dem = None
