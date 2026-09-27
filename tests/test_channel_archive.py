import netCDF4 as nc
import numpy as np
import pytest

from hydro_ops.wrf_hydro.channel_archive import publish_hourly


def native(path, hour, reference=0, ids=(10, 20, 30)):
    with nc.Dataset(path, 'w') as ds:
        for name, size in [('time', None), ('feature_id', 3), ('reference_time', 1)]:
            ds.createDimension(name, size)
        t = ds.createVariable('time', 'i4', ('time',))
        t.units = 'hours since 1979-02-01 00:00:00'
        t[:] = [hour]
        t.valid_min = hour
        t.valid_max = hour
        ds.createVariable('reference_time', 'i4', ('reference_time',))[:] = [reference]
        ds.createVariable('feature_id', 'i8', ('feature_id',))[:] = ids
        packed = ds.createVariable('packed_diagnostic', 'i2', ('feature_id',), fill_value=-32768)
        packed.scale_factor = np.float32(0.1)
        packed.add_offset = np.float32(1.)
        packed.set_auto_maskandscale(False)
        packed[:] = [hour, hour + 1, -32768]
        for name in ('streamflow', 'velocity', 'qBtmVertRunoff'):
            v = ds.createVariable(name, 'f4', ('feature_id',), fill_value=-9999.)
            v[:] = [hour + 1, hour * 2, -9999.]
            v.units = 'test'


def test_native_stack_and_midnight_merge(tmp_path):
    paths = []
    for hour in range(24):
        path = tmp_path / f'19790201{hour:02d}00.CHRTOUT_DOMAIN1'
        native(path, hour, reference=0 if hour == 0 else 1)
        paths.append(path)
    dest = tmp_path / 'out'
    publish_hourly(paths[:1], dest)
    publish_hourly(paths[1:], dest)
    with nc.Dataset(dest / '1979/02/19790201.CHRTOUT_DOMAIN1') as ds:
        assert ds['streamflow'].dimensions == ('time', 'feature_id')
        assert ds['streamflow'].shape == (24, 3)
        np.testing.assert_array_equal(ds['streamflow'][:, 0], np.arange(1, 25))
        np.testing.assert_array_equal(ds['velocity'][:, 1], 2 * np.arange(24))
        assert np.ma.getmaskarray(ds['streamflow'][:, 2]).all()
        np.testing.assert_array_equal(ds['reference_time'][:], [0] + [1] * 23)
        assert ds['feature_id'].dimensions == ('feature_id',)
        assert ds.channel_archive_policy == 'explicit_record_stack_v1'
        ds['packed_diagnostic'].set_auto_maskandscale(False)
        np.testing.assert_array_equal(ds['packed_diagnostic'][:, 0], np.arange(24))
        np.testing.assert_array_equal(ds['packed_diagnostic'][:, 2], [-32768] * 24)
    with pytest.raises(ValueError, match='Refusing overlap'):
        publish_hourly(paths, dest)


def test_static_alignment_and_time_gaps_fail(tmp_path):
    first = tmp_path / '197902010100.CHRTOUT_DOMAIN1'
    second = tmp_path / '197902010200.CHRTOUT_DOMAIN1'
    native(first, 1)
    native(second, 2, ids=(20, 10, 30))
    with pytest.raises(AssertionError):
        publish_hourly([first, second], tmp_path / 'bad')
    assert not (tmp_path / 'bad/1979/02/19790201.CHRTOUT_DOMAIN1').exists()
    native(second, 3)
    with pytest.raises(ValueError, match='gapped'):
        publish_hourly([first, second], tmp_path / 'gap')
