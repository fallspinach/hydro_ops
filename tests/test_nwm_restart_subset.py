import importlib
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def test_restart_reach_mapping_and_raw_crop(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('subset_nwm_restart')
    indices = tool.reach_indices(np.array([50, 10, 30]), np.array([30, 50]))
    np.testing.assert_array_equal(indices, [2, 0])
    with pytest.raises(ValueError, match='missing'):
        tool.reach_indices(np.array([1, 2]), np.array([3]))
    with pytest.raises(ValueError, match='Duplicate'):
        tool.reach_indices(np.array([1, 1]), np.array([1]))
    src, dst = tmp_path / 'source.nc', tmp_path / 'target.nc'
    with Dataset(src, 'w') as data:
        for name, size in [('iy', 3), ('ix', 4), ('links', 3)]:
            data.createDimension(name, size)
        data.createVariable('soil', 'f4', ('iy', 'ix'))[:] = np.arange(12).reshape(3, 4)
        data.createVariable('z_gwsubbas', 'f4', ('links',))[:] = [5, 1, 3]
        data.his_out_counts = 100
        data.Restart_Time = '1979-02-01_00:00:00'
    tool.copy_restart(src, dst, {'iy': slice(1, 3), 'ix': slice(0, 2), 'links': indices},
                      {'iy': 2, 'ix': 2, 'links': 2})
    with Dataset(dst) as data:
        np.testing.assert_array_equal(data['soil'][:], [[4, 5], [8, 9]])
        np.testing.assert_array_equal(data['z_gwsubbas'][:], [3, 5])
        assert data.his_out_counts == 100
        assert data.Restart_Time == '1979-02-01_00:00:00'
