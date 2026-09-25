"""Exercise the scheduled-summary command's repeat and month-boundary invalidation."""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from test_forcing_temporal_summary import NAMES, REDUCERS, UNITS, chunk

from hydro_ops.forcing.nrt_cycle import identity
from hydro_ops.forcing.temporal_summary import summary_path


def test_nrt_summary_refresh_month_boundary(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('refresh_test',
        Path(__file__).resolve().parents[1] / 'bin/backfill_forcing_summaries.py')
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    raw, output = tmp_path / 'hourly', tmp_path / 'summary'
    start, end = date(2026, 1, 1), date(2026, 2, 28)
    paths = {}
    for i in range(60):
        day = start + timedelta(days=i)
        p = chunk(raw, day)
        paths[day] = p
        p.with_name(p.name+'.nrt-receipt.json').write_text(json.dumps({
            'status': 'passed', 'sha256': 'fixture', 'published_identity': identity(p)}))
    class SerialPool:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def map(self, function, tasks):
            return map(function, tasks)
    monkeypatch.setattr(command, 'ProcessPoolExecutor', SerialPool)
    monkeypatch.setattr(command, 'load_forcing_reducers', lambda p: (REDUCERS, NAMES, UNITS))
    monkeypatch.setattr('sys.argv', ['backfill', '--stream', 'nrt', '--input-root', str(raw),
        '--output-root', str(output), '--start', str(start), '--end', str(end), '--complete-months-only'])
    command.main()
    originals = {p: identity(p) for p in output.rglob('*.LDASIN_DOMAIN1.*')}
    command.main()
    assert all(identity(p) == previous for p, previous in originals.items())
    revised = paths[date(2026, 2, 1)]
    with Dataset(revised, 'a') as ds:
        ds['T2D'][0, 0, 0] = 304  # January 31's last endpoint.
        ds['T2D'][1, 0, 0] = 328  # February 1's first endpoint.
        ds.revision = 'new hourly publication'
    revised.with_name(revised.name+'.nrt-receipt.json').write_text(json.dumps({
        'status': 'passed', 'sha256': 'revised-fixture', 'published_identity': identity(revised)}))
    command.main()
    expected = {summary_path(output / 'daily', date(2026, 1, 31)),
                summary_path(output / 'daily', date(2026, 2, 1)),
                summary_path(output / 'monthly', start, monthly=True),
                summary_path(output / 'monthly', date(2026, 2, 1), monthly=True)}
    assert {p for p, previous in originals.items() if identity(p) != previous} == expected
    for day, value in [(date(2026, 1, 31), 281), (date(2026, 2, 1), 282)]:
        with Dataset(summary_path(output / 'daily', day)) as ds:
            np.testing.assert_allclose(ds['T2D'][0, 0, 0], value)
    for day, value in [(start, 280+1/31), (date(2026, 2, 1), 280+2/28)]:
        with Dataset(summary_path(output / 'monthly', day, monthly=True)) as ds:
            np.testing.assert_allclose(ds['T2D'][0, 0, 0], value, atol=3e-5)
            assert ds.forcing_stream == 'nrt'
