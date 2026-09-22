import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.nwm_masks import derive_masks, mask_digest


def test_preserves_inactive_forcing_and_excludes_outside():
    model, forcing = derive_masks([[1, 1, 0]], [[1, 0, 1]], [[1, 1, 1]])
    np.testing.assert_array_equal(model, [[1, 0, 0]])
    np.testing.assert_array_equal(forcing, [[1, 1, 0]])


def test_missing_active_coverage_rejected():
    with pytest.raises(ValueError, match='lack forcing'):
        derive_masks([[1]], [[1]], [[0]])


def test_masks_must_align():
    with pytest.raises(ValueError, match='matching'):
        derive_masks([[1]], [[1, 0]], [[1]])


def test_forcing_subset_exact_values_and_active_failure(tmp_path, monkeypatch):
    import importlib
    import json
    import shutil
    from pathlib import Path

    if not shutil.which('ncks'):
        pytest.skip('NCO required for integration test')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('subset_nwm_forcing')
    lat, lon = np.meshgrid(np.arange(4), np.arange(3))
    source, mask, output = (tmp_path / n for n in ('source.nc', 'mask.nc', 'output.nc'))
    with Dataset(source, 'w') as d:
        for dim, size in [('time', 2), ('y', 3), ('x', 4)]:
            d.createDimension(dim, size)
        d.createVariable('time', 'f8', ('time',))[:] = [0, 1]
        for name, values in [('lat', lat), ('lon', lon)]:
            d.createVariable(name, 'f4', ('y', 'x'))[:] = values
        for name in tool.FIELDS:
            d.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999)[:] = np.arange(24).reshape(2, 3, 4)
        d.forcing_static_mask_status = 'parent evidence'
    with Dataset(mask, 'w') as d:
        d.createDimension('y', 2); d.createDimension('x', 2)
        d.source_window = json.dumps({'west_east_start': 1, 'west_east_end': 2,
                                     'south_north_start': 0, 'south_north_end': 1})
        for name, values in [('lat', lat[:2, 1:3]), ('lon', lon[:2, 1:3])]:
            d.createVariable(name, 'f4', ('y', 'x'))[:] = values
        for name, values in [('boundary_mask', [[1, 1], [1, 1]]), ('model_mask', [[1, 0], [0, 0]]),
                             ('forcing_mask', [[1, 1], [0, 0]])]:
            v = d.createVariable(name, 'u1', ('y', 'x')); v[:] = values; v.sha256 = mask_digest(values)
    assert tool.subset(source, output, mask)['retained_values_exact_match']
    with Dataset(output) as d:
        assert d['T2D'][0, 0, 1] == 2  # Inactive-but-retained forcing cell.
        assert np.ma.getmaskarray(d['T2D'][0])[1].all()
        assert 'forcing_static_mask_status' not in d.ncattrs()
    with Dataset(source, 'r+') as d:
        d['T2D'][0, 0, 1] = -9999
    failed = tmp_path / 'failed.nc'
    with pytest.raises(ValueError, match='missing active'):
        tool.subset(source, failed, mask)
    assert not failed.exists()
