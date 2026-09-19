import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing.gfs_publication import write_changed_chunks


@pytest.mark.parametrize('chunks', [(1, 2, 3), (2, 2, 3)])
def test_sparse_write_retains_exact_values_and_edge_chunks(tmp_path, chunks):
    path = tmp_path / 'test.nc'
    original = np.arange(35, dtype='f4').reshape(5, 7)
    original[1, 1] = np.nan
    updated = original.copy()
    updated[0, 0] = -0.0
    updated[-1, -1] = -9999
    with Dataset(path, 'w') as data:
        for name, size in [('time', 2), ('y', 5), ('x', 7)]:
            data.createDimension(name, size)
        var = data.createVariable('field', 'f4', ('time', 'y', 'x'),
                                  zlib=True, chunksizes=chunks, fill_value=-9999)
        var[:] = np.stack([original, original])
        write_changed_chunks(var, 0, original, updated)
    with Dataset(path) as data:
        data.set_auto_maskandscale(False)
        assert data['field'][0].tobytes() == updated.tobytes()
        assert data['field'][1].tobytes() == original.tobytes()
