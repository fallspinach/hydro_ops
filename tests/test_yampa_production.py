import importlib
import json
from pathlib import Path

import pytest


def test_yampa_isolated_and_requires_gate(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    runner = importlib.import_module('run_cnrfc_production')
    outputs, restarts = runner.publication_roots(tmp_path, 'production_v1', domain='yampa')
    assert outputs == tmp_path / 'nwm/outputs/yampa/retro/production_v1'
    assert restarts == tmp_path / 'nwm/restarts/yampa/retro/production_v1'
    with pytest.raises(ValueError, match='Unsupported'):
        runner.publication_roots(tmp_path, 'production_v1', 'nrt', 'yampa')
    with pytest.raises(ValueError, match='warm-start gate'):
        runner.run(tmp_path, 1979, campaign='production_v1', domain='yampa')


def test_yampa_approval_and_hash_guard(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('run_yampa_production')
    domain = tmp_path / 'nwm/static/domains/yampa'
    (domain / 'masks').mkdir(parents=True); (domain / 'boundaries').mkdir()
    mask = domain / 'masks/yampa_masks.nc'; mask.write_bytes(b'mask')
    boundary = domain / 'boundaries/yampa_boundaries.gpkg'; boundary.write_bytes(b'boundary')
    record = {'status': 'passed', 'domain': 'yampa', 'approved_for_production': True,
              'mask_sha256': module.digest(mask), 'boundary_sha256': module.digest(boundary)}
    manifest = domain / 'manifest.json'; manifest.write_text(json.dumps(record))
    assert module.approved(tmp_path) == domain
    record['approved_for_production'] = False; manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='approval'):
        module.approved(tmp_path)
    record['approved_for_production'] = True; manifest.write_text(json.dumps(record))
    mask.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        module.approved(tmp_path)
