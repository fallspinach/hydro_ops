import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    return importlib.import_module('backfill_ucrb_forcing')


def test_approval_and_hashes(runner, tmp_path):
    (tmp_path / 'masks').mkdir()
    (tmp_path / 'boundaries').mkdir()
    mask = tmp_path / 'masks/ucrb_masks.nc'
    boundary = tmp_path / 'boundaries/ucrb_boundaries.gpkg'
    mask.write_bytes(b'mask'); boundary.write_bytes(b'boundary')
    record = {'status': 'passed', 'domain': 'ucrb', 'approved_for_production': False,
              'mask_sha256': runner.digest(mask), 'boundary_sha256': runner.digest(boundary)}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='unapproved'):
        runner.approved(tmp_path)
    record['approved_for_production'] = True
    path.write_text(json.dumps(record)); runner.approved(tmp_path)
    mask.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        runner.approved(tmp_path)


def test_hourly_gaps(runner, tmp_path):
    paths = [tmp_path / f'retro/hourly/2026/03/2026030{d}.LDASIN_DOMAIN1' for d in (2, 4)]
    assert runner.hourly_gaps(paths, tmp_path, 'retro') == ['2026-03-03']
    paths.append(tmp_path / 'retro/daily/2026/03/20260303.LDASIN_DOMAIN1.daily')
    assert runner.hourly_gaps(paths, tmp_path, 'retro') == ['2026-03-03']
    with pytest.raises(ValueError, match='No nrt'):
        runner.hourly_gaps(paths, tmp_path, 'nrt')
