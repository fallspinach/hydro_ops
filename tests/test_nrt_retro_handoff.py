"""Small-grid NRT baseline to stable-PRISM retro CLI integration."""
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset
from test_chunk_archive import hourly_chunks

from hydro_ops.forcing.daily_archive import create_daily_archive

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('layouts', [(True, True, True), (False, False, False), (False, True, True)])
def test_nrt_baseline_stable_retro_handoff(tmp_path, layouts):
    baselines = []
    for day, preserve in enumerate(layouts, 1):
        hours = []
        for hour in range(24):
            path = tmp_path / f'hour-{day}-{hour}.nc'
            hourly_chunks(path, (day - 1) * 24 + hour)
            with Dataset(path, 'r+') as data:
                data.createVariable('RAINRATE', 'f4', ('time', 'y', 'x'),
                                    zlib=True, complevel=2, chunksizes=(1, 2, 3))[:] = 1 / 3600
            hours.append(path)
        baseline = tmp_path / f'baseline-{day}.nc'
        create_daily_archive(hours, baseline, date(2000, 1, day),
                             chunk_copy=preserve, preserve_source_chunks=preserve)
        baselines.append(baseline)
    original = [path.read_bytes() for path in baselines]
    weights, prism = tmp_path / 'weights.nc', tmp_path / 'prism.nc'
    with Dataset(weights, 'w') as data:
        data.createDimension('links', 35)
        data.createDimension('rank', 1)
        for name in ('src_address', 'dst_address'):
            data.createVariable(name, 'i4', ('links',))[:] = np.arange(1, 36)
        data.createVariable('remap_matrix', 'f8', ('links',))[:] = 1
        for name in ('src_grid_dims', 'dst_grid_dims'):
            data.createVariable(name, 'i4', ('rank',))[:] = 35
    with Dataset(prism, 'w') as data:
        data.createDimension('y', 5)
        data.createDimension('x', 7)
        data.createVariable('ppt', 'f4', ('y', 'x'))[:] = 48
    env = {**os.environ, 'HYDRO_OPS_ARCHIVE_CHUNKS': '1',
           'HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS': '1'}
    windows = tmp_path / 'windows'
    for day in (2, 3):
        window = windows / f'2000/01/200001{day:02d}.LDASIN_DOMAIN1'
        window.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(ROOT / 'bin/reconcile_prism_precipitation_day.py'),
                   *map(str, [baselines[day - 2]] * 12 + [baselines[day - 1]] * 12),
                   '--hour-indices', *map(str, [*range(12, 24), *range(12)]),
                   '--prism', str(prism), '--weights', str(weights), '--daily-output', str(window),
                   '--day', f'2000-01-{day:02d}', '--revision', 'stable',
                   '--diagnostics', str(tmp_path / f'diagnostics-{day}.json')]
        subprocess.run(command, env=env, check=True, capture_output=True, text=True)
        with Dataset(window, 'r+') as data:
            # The production wrapper labels accepted windows after reconciliation.
            data.forcing_stream = 'retro'
    output = tmp_path / 'retro'
    subprocess.run([sys.executable, str(ROOT / 'bin/materialize_calendar_forcing.py'),
                    '--input-root', str(windows), '--output-root', str(output),
                    '--start', '2000-01-02', '--days', '1', '--stream', 'retro',
                    '--require-accepted-prism-windows'], env=env, check=True,
                   capture_output=True, text=True)
    with Dataset(output / '20000102.LDASIN_DOMAIN1') as data, Dataset(baselines[1]) as baseline:
        np.testing.assert_allclose(data['RAINRATE'][:] * 3600, 2, rtol=1e-6)
        for name in baseline.variables:
            if name != 'RAINRATE':
                np.testing.assert_array_equal(data[name][:], baseline[name][:])
        assert data.forcing_stream == 'retro'
        assert data.archive_granularity == 'utc_calendar_day'
        assert 'stable' in data.prism_precipitation_revisions
    assert [path.read_bytes() for path in baselines] == original
