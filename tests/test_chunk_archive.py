import json
from datetime import date

import numpy as np
import pytest
from netCDF4 import Dataset
from test_static_forcing_mask_pilot import fixture_files

from hydro_ops.forcing.chunk_archive import assemble
from hydro_ops.forcing.daily_archive import create_daily_archive


def test_chunk_assembly_matches_reference(tmp_path):
    source, _ = fixture_files(tmp_path, chunksizes=(1, 2, 2))
    with Dataset(source, 'r+') as data:
        data['time'].units = 'hours since 2000-01-01'
    paths = [source, source]
    indices = [0, 1]
    day = date(2000, 1, 1)
    expected, actual = tmp_path/'reference.nc', tmp_path/'fast.nc'
    create_daily_archive(paths, expected, day, expected_hours=2, source_time_indices=indices)
    result = assemble(paths, indices, actual, day, tmp_path/'work', expected_hours=2)
    assert result['status'] == 'passed'
    with Dataset(expected) as a, Dataset(actual) as b:
        for name in a.variables:
            np.testing.assert_array_equal(a[name][:], b[name][:])
    with pytest.raises(ValueError, match='separate'):
        assemble(paths, indices, actual, day, tmp_path/'work', expected_hours=2)


@pytest.mark.parametrize('time_chunk', [1, 2])
def test_opt_in_overrides_and_fallback(tmp_path, time_chunk):
    source, _ = fixture_files(tmp_path, chunksizes=(time_chunk, 2, 2))
    with Dataset(source, 'r+') as data:
        data['time'].units = 'hours since 2000-01-01'
    values = np.arange(8, dtype='f8').reshape(2, 2, 2)+275.1
    values[0, 0, 1] = np.nan
    paths, indices = [source]*2, [0, 1]
    kwargs = {'expected_hours': 2, 'source_time_indices': indices, 'time_variable_overrides': {'T2D': values},
              'global_attributes': {'prism_reconciliation_accepted': 'true'}, 'verification': 'targeted'}
    a, b = tmp_path/'reference.nc', tmp_path/'fast.nc'
    create_daily_archive(paths, a, date(2000, 1, 1), **kwargs)
    create_daily_archive(paths, b, date(2000, 1, 1), chunk_copy=True, **kwargs)
    with Dataset(a) as first, Dataset(b) as second:
        for name in first.variables:
            np.testing.assert_array_equal(first[name][:], second[name][:])
        assert second.prism_reconciliation_accepted == 'true'
    manifest = json.loads(b.with_suffix('.nc.manifest.json').read_text())
    assert manifest['verified']
    assert (manifest.get('archive_writer') == 'compressed_chunks') == (time_chunk == 1)
    assert manifest['fully_verified_overrides'] == ['T2D']


@pytest.mark.parametrize('present', [(False, True), (True, False), (False, False), (True, True)])
def test_normalized_provenance_with_coupled_overrides(tmp_path, present):
    paths = []
    for i, has_timing in enumerate(present):
        folder = tmp_path / str(i)
        folder.mkdir()
        source, _ = fixture_files(folder, chunksizes=(1, 2, 2))
        with Dataset(source, 'r+') as data:
            data['time'].units = 'hours since 2003-01-01'
            data['time'][:] = [2*i, 2*i+1]
            ref = data.createVariable('precip_source_id', 'u1', ('time', 'y', 'x'),
                                      zlib=True, complevel=2, chunksizes=(1, 2, 2))
            ref[:] = 7
            if has_timing:
                timing = data.createVariable('precip_timing_source_id', 'u1', ref.dimensions,
                                             zlib=True, complevel=2, chunksizes=(1, 2, 2))
                timing[:] = [[[5, 6], [0, 255]], [[6, 5], [0, 255]]]
        paths.append(source)
    originals = [p.read_bytes() for p in paths]
    changes = {name: np.arange(16, dtype='f4').reshape(4, 2, 2) + i
               for i, name in enumerate(('RAINRATE', 'T2D', 'Q2D', 'LWDOWN'))}
    changes['Q2D'][1, 1, 1] = np.nan
    kwargs = {'expected_hours': 4, 'source_time_indices': [0, 1, 0, 1],
              'normalize_precipitation_timing': True, 'time_variable_overrides': changes,
              'verification': 'targeted', 'fully_verified_overrides': {'RAINRATE'}}
    reference, optimized = tmp_path/'reference.nc', tmp_path/'optimized.nc'
    inputs = [paths[0]]*2 + [paths[1]]*2
    create_daily_archive(inputs, reference, date(2003, 1, 1), **kwargs)
    create_daily_archive(inputs, optimized, date(2003, 1, 1), chunk_copy=True, **kwargs)
    with Dataset(reference) as a, Dataset(optimized) as b:
        for name in a.variables:
            assert a[name].dtype == b[name].dtype
            for attr in a[name].ncattrs():
                np.testing.assert_array_equal(a[name].getncattr(attr), b[name].getncattr(attr))
            np.testing.assert_array_equal(np.ma.getmaskarray(a[name][:]), np.ma.getmaskarray(b[name][:]))
            a[name].set_auto_maskandscale(False)
            b[name].set_auto_maskandscale(False)
            assert np.asarray(a[name][:]).tobytes() == np.asarray(b[name][:]).tobytes()
    manifest = json.loads(optimized.with_suffix('.nc.manifest.json').read_text())
    assert manifest['archive_writer'] == 'compressed_chunks'
    assert manifest['fully_verified_overrides'] == sorted(changes)
    assert manifest['timing']['normalized_variables'] == ['precip_timing_source_id']
    assert [p.read_bytes() for p in paths] == originals
