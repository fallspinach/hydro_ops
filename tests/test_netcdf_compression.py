from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.download.netcdf_compression import compress_netcdf, is_compressed_netcdf


def test_compress_netcdf_is_lossless(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    destination = tmp_path / "destination.nc"
    work = tmp_path / "scratch"
    values = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    with Dataset(source, "w", format="NETCDF3_CLASSIC") as dataset:
        for name, length in (("time", 2), ("y", 3), ("x", 4)):
            dataset.createDimension(name, length)
        variable = dataset.createVariable("field", "f4", ("time", "y", "x"))
        variable.units = "K"
        variable[:] = values
    assert not is_compressed_netcdf(source)
    compress_netcdf(source, destination, work_directory=work)
    assert is_compressed_netcdf(destination)
    with Dataset(destination) as dataset:
        np.testing.assert_array_equal(dataset["field"][:], values)
        assert dataset["field"].units == "K"
    assert not list(work.iterdir())


def test_migration_candidates_exclude_transient_files(tmp_path: Path) -> None:
    spec = spec_from_file_location("compress_script", "bin/compress_forcing_netcdf.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    stable = tmp_path / "hour.nc"
    stable.touch()
    for name in ("hour.nc.part.wgrib2.nc", "hour.nc.repacked", "hour.nc.compressing"):
        (tmp_path / name).touch()
    assert module.candidates([tmp_path]) == [stable]
