from __future__ import annotations

from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from osgeo import gdal, osr

from hydro_ops.forcing.elevation import create_target_elevation


def write_dem(path: Path) -> None:
    driver = gdal.GetDriverByName("GTiff")
    raster = driver.Create(str(path), 4, 3, 1, gdal.GDT_Float32)
    raster.SetGeoTransform((-2.0, 1.0, 0.0, 3.0, 0.0, -1.0))
    reference = osr.SpatialReference()
    reference.ImportFromEPSG(4326)
    raster.SetProjection(reference.ExportToWkt())
    raster.GetRasterBand(1).SetNoDataValue(-9999.0)
    raster.GetRasterBand(1).WriteArray(
        np.array([[10, 20, 30, 40], [20, 30, 40, 50], [30, 40, 50, 60]], dtype=np.float32)
    )
    raster = None


def write_target(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("y", 1)
        data.createDimension("x", 3)
        data.createVariable("lon", "f8", ("y", "x"))[:] = [[-1.5, -1.0, 0.5]]
        data.createVariable("lat", "f8", ("y", "x"))[:] = [[2.5, 2.0, 1.5]]
        data.createVariable("active_domain", "i1", ("y", "x"))[:] = [[1, 1, 0]]


def test_create_target_elevation_bilinear_and_masked(tmp_path: Path) -> None:
    dem = tmp_path / "dem.tif"
    target = tmp_path / "target.nc"
    output = tmp_path / "elevation.nc"
    write_dem(dem)
    write_target(target)
    create_target_elevation(dem, target, output)
    with Dataset(output) as data:
        values = data["elevation"][:]
        assert values[0, 0] == 10.0
        assert values[0, 1] == 20.0
        assert values.mask[0, 2]
        assert data["elevation"].units == "m"
