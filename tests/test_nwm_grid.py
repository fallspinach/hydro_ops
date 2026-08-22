from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.nwm_grid import create_scrip_grid, extract_target_grid


def sample(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.createDimension("nv4", 4)
        lon = data.createVariable("lon", "f8", ("y", "x"))
        lat = data.createVariable("lat", "f8", ("y", "x"))
        lon_bnds = data.createVariable("lon_bnds", "f8", ("y", "x", "nv4"))
        lat_bnds = data.createVariable("lat_bnds", "f8", ("y", "x", "nv4"))
        forcing = data.createVariable("T2D", "f4", ("time", "y", "x"), fill_value=-9999.0)
        lon[:] = np.arange(6).reshape(2, 3)
        lat[:] = np.arange(6).reshape(2, 3) + 30
        lon_bnds[:] = np.repeat(lon[:][..., None], 4, axis=2)
        lat_bnds[:] = np.repeat(lat[:][..., None], 4, axis=2)
        forcing[:] = np.ma.array(
            [[[1, 2, 3], [4, 5, 6]]],
            mask=[[[False, True, False], [False, False, True]]],
        )


def test_extract_target_and_scrip_grid(tmp_path):
    source = tmp_path / "sample.nc"
    target = tmp_path / "target.nc"
    scrip = tmp_path / "scrip.nc"
    sample(source)
    extract_target_grid(source, target)
    create_scrip_grid(target, scrip)
    with Dataset(target) as data:
        assert data["active_domain"][:].tolist() == [[1, 0, 1], [1, 1, 0]]
        assert data["lon"].shape == (2, 3)
    with Dataset(scrip) as data:
        assert data["grid_dims"][:].tolist() == [3, 2]
        assert data["grid_imask"][:].tolist() == [1, 0, 1, 1, 1, 0]
        assert data["grid_center_lat"].shape == (6,)
        assert data["grid_corner_lon"].shape == (6, 4)
