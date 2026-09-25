from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.experiments import nrt_efficiency as experiment
from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.native_donor import FIELDS


def fixture(path, *, outside=False, hole=False, hours=24):
    with Dataset(path, 'w') as ds:
        for name, size in [('time', hours), ('y', 2), ('x', 3)]:
            ds.createDimension(name, size)
        t = ds.createVariable('time', 'f8', ('time',))
        t.units = 'hours since 2026-09-20'
        t[:] = np.arange(hours)
        ds.cnrfc_stage4_policy = 'test'
        ds.prism_reconciliation_accepted = 'true'
        ds.history = 'original'
        for name in FIELDS:
            v = ds.createVariable(name, 'f4', ('time', 'y', 'x'), fill_value=-9999,
                                  zlib=True, complevel=2, chunksizes=(1, 2, 3))
            v[:] = 1
            v[:, 1, 2] = 1 if outside else -9999
            if hole:
                v[0, 0, 0] = -9999


@pytest.mark.parametrize('outside', [False, True])
def test_sparse_audit_matches_reference(tmp_path, outside):
    paths = [tmp_path / f'{i}.nc' for i in range(2)]
    for p in paths:
        fixture(p, outside=outside)
    keep = np.ones((2, 3), dtype=bool)
    keep[1, 2] = False
    engine = SimpleNamespace(repair=SimpleNamespace(active=keep, keep=keep))
    day = date(2026, 9, 20)
    production_writes = nrt_cycle.RecentNrt.audit_final(engine, paths[0], day, True)
    writes = experiment.audit_final_sparse(engine, paths[1], day, True)
    assert writes == (24 * len(FIELDS) if outside else 0)
    assert production_writes == writes
    # Calendar completeness metadata was added after the original prototype.
    with Dataset(paths[1], 'a') as ds:
        ds.calendar_day_complete = 'true'
        ds.hourly_source_count = 24
    assert experiment.same_input_content(*paths)


def test_sparse_audit_still_rejects_active_holes(tmp_path):
    p = tmp_path / 'hole.nc'
    fixture(p, hole=True)
    keep = np.ones((2, 3), dtype=bool)
    keep[1, 2] = False
    with pytest.raises(ValueError, match='active holes'):
        experiment.audit_final_sparse(SimpleNamespace(repair=SimpleNamespace(active=keep, keep=keep)),
                                      p, date(2026, 9, 20), True)


def test_partial_audit_requires_exact_contiguous_hours(tmp_path):
    p = tmp_path / 'partial.nc'
    fixture(p, hours=13)
    keep = np.ones((2, 3), dtype=bool)
    keep[1, 2] = False
    engine = SimpleNamespace(repair=SimpleNamespace(active=keep, keep=keep))
    nrt_cycle.RecentNrt.audit_final(engine, p, date(2026, 9, 20), False, expected_hours=13)
    with Dataset(p) as ds:
        assert ds.calendar_day_complete == 'false'
        assert ds.hourly_source_count == 13
    with pytest.raises(ValueError):
        nrt_cycle.RecentNrt.audit_final(engine, p, date(2026, 9, 20), False)
    with Dataset(p, 'a') as ds:
        ds['time'][6] = 7
    with pytest.raises(ValueError):
        nrt_cycle.RecentNrt.audit_final(engine, p, date(2026, 9, 20), False, expected_hours=13)


def test_input_content_rejects_values_and_units_not_history(tmp_path):
    a, b = tmp_path / 'a.nc', tmp_path / 'b.nc'
    fixture(a)
    fixture(b)
    with Dataset(b, 'a') as ds:
        ds.history = 'new download metadata'
    assert experiment.same_input_content(a, b)
    with Dataset(b, 'a') as ds:
        ds['T2D'][0, 0, 0] = 2
    assert not experiment.same_input_content(a, b)
    with Dataset(b, 'a') as ds:
        ds['T2D'][0, 0, 0] = 1
        ds['T2D'].units = 'wrong'
    assert not experiment.same_input_content(a, b)


def test_mixed_precip_prepared_once_at_same_worker_count(tmp_path, monkeypatch):
    prepares, builds = [], []
    original = lambda *args, **kwargs: builds.append(kwargs)
    monkeypatch.setattr(nrt_cycle, 'produce_complete_day', original)
    def prepare(day, layout, directory, work, workers):
        prepares.append(workers)
        directory.mkdir()
        (directory / 'cache.json').write_text('{}')
    monkeypatch.setattr(experiment, 'prepare_precipitation', prepare)
    with experiment.shared_mixed_precipitation():
        for first, last in [(0, 12), (13, 23)]:
            nrt_cycle.produce_complete_day(date(2026, 9, 20), None, Path('unused'),
                work_directory=tmp_path, start_hour=first, end_hour=last, precipitation_remap_workers=4)
    assert prepares == [4]
    assert builds[0]['precipitation_cache'] == builds[1]['precipitation_cache']
    assert nrt_cycle.produce_complete_day is original
