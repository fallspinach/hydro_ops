"""Small-file checks for the isolated end-to-end benchmark harness."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset
from test_static_forcing_mask_pilot import fixture_files


@pytest.mark.parametrize('method', ['chunks', 'cdo'])
def test_benchmark_file(tmp_path, monkeypatch, method):
    directory = Path(__file__).parents[1]/'bin'
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('parallel_benchmark', directory/'benchmark_static_mask_parallel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cdo = Path('/home/mpan/local/miniforge3/bin/cdo')
    if method == 'cdo' and not cdo.exists():
        pytest.skip('CDO not installed')
    source, mask = fixture_files(tmp_path, chunksizes=(1, 1, 1))
    for path in (source, mask):
        with Dataset(path, 'r+') as data:
            data['lat'][:] = [[40, 40], [41, 41]]
            data['lon'][:] = [[-101, -100], [-101, -100]]
            data['lat'].units = 'degrees_north'
            data['lon'].units = 'degrees_east'
            for var in data.variables.values():
                if var.name not in ('lat', 'lon', 'time'):
                    var.coordinates = 'lat lon'
            if path == source:
                data['time'].units = 'hours since 2000-01-01 00:00:00'
                data['time'].calendar = 'standard'
    before = source.read_bytes()
    result = module.run_file((source, mask, tmp_path, tmp_path/'result.nc', method, cdo))
    assert result['status'] == 'passed'
    assert source.read_bytes() == before
    with Dataset(tmp_path/'result.nc') as data:
        assert np.ma.getmaskarray(data['T2D'][:])[:, 1, :].all()
