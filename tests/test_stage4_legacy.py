from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.download.stage4_legacy import write_legacy_hourly_netcdf


def test_write_legacy_hourly_uses_canonical_grid_and_masks_missing(tmp_path: Path) -> None:
    template = tmp_path / "template.nc"
    with Dataset(template, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.createVariable("y", "f8", ("y",))[:] = [0, 1]
        data.createVariable("x", "f8", ("x",))[:] = [0, 1, 2]
        data.createVariable("latitude", "f8", ("y", "x"))[:] = 40
        data.createVariable("longitude", "f8", ("y", "x"))[:] = -105
    output = tmp_path / "hour.nc"
    write_legacy_hourly_netcdf(
        output,
        template,
        np.array([0, 1, 2, 3, 4, 9.999e20], dtype=np.float32),
        datetime(2002, 1, 1, tzinfo=UTC),
        "ST4.2002010100.01h.Z",
    )
    with Dataset(output) as data:
        assert data["APCP_surface"].shape == (1, 2, 3)
        assert np.ma.getmaskarray(data["APCP_surface"][:]).sum() == 1
        assert data["time"][0] == datetime(2002, 1, 1, tzinfo=UTC).timestamp()
        assert data.getncattr("source_grib_edition") == 1
