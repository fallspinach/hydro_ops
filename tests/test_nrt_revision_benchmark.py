import importlib
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def test_prism_perturbation_changes_wet_cells_only(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('benchmark_nrt_revisions')
    path = tmp_path / 'prism.nc'
    with Dataset(path, 'w') as data:
        data.createDimension('x', 3)
        data.createVariable('ppt', 'f4', ('x',), fill_value=-9999)[:] = [10, 0, -9999]
    assert module.perturb_precipitation(path) == 1
    with Dataset(path) as data:
        np.testing.assert_allclose(data['ppt'][:2], [10.1, 0])
        assert np.ma.getmaskarray(data['ppt'][:])[2]


def test_temperature_revision_preserves_missing_cells(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('benchmark_nrt_revisions')
    path = tmp_path / 'prism.nc'
    with Dataset(path, 'w') as data:
        data.createDimension('x', 3)
        data.createVariable('tmin', 'f4', ('x',), fill_value=-9999)[:] = [-10, 0, -9999]
    assert module.perturb_temperature(path, 'tmin') == 2
    with Dataset(path) as data:
        np.testing.assert_allclose(data['tmin'][:2], [-9, 1])
        assert np.ma.getmaskarray(data['tmin'][:])[2]
