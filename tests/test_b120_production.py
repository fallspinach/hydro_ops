import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    return importlib.import_module('run_b120_production')


def test_isolated_publication(runner, tmp_path):
    shared = importlib.import_module('run_cnrfc_production')
    outputs, restarts = shared.publication_roots(tmp_path, runner.CAMPAIGN, domain='b120')
    assert outputs == tmp_path / 'nwm/outputs/b120/retro/production_v1'
    assert restarts == tmp_path / 'nwm/restarts/b120/retro/production_v1'
    for stream, campaign in [('nrt', runner.CAMPAIGN), ('retro', 'hu18_pilot_v1')]:
        with pytest.raises(ValueError, match='Unsupported'):
            shared.publication_roots(tmp_path, campaign, stream, 'b120')
    with pytest.raises(ValueError, match='warm-start gate'):
        shared.run(tmp_path, 1979, campaign=runner.CAMPAIGN, domain='b120')


def test_wrong_domain_gate_rejected(runner, tmp_path):
    gate = tmp_path / 'gate.json'
    gate.write_text(json.dumps({'status': 'passed', 'domain': 'cnrfc_hu18_v1',
                                'spinup_warm_start_verified': True}))
    with pytest.raises(ValueError, match='warm-start gate'):
        runner.validate_gate(tmp_path, tmp_path, gate)


def test_changed_initial_restart_rejected(runner, tmp_path):
    domain = tmp_path / 'domain'
    (domain / 'masks').mkdir(parents=True)
    mask = domain / 'masks/b120_masks.nc'
    mask.write_bytes(b'mask')
    initial = tmp_path / 'nwm/runs/b120/retro/production_v1/initialization'
    initial.mkdir(parents=True)
    names = runner.restart_names(runner.datetime(1979, 1, 2))
    for name in names:
        (initial / name).write_bytes(b'restart')
    gate = tmp_path / 'gate.json'
    gate.write_text(json.dumps({'status': 'passed', 'domain': 'b120',
        'spinup_warm_start_verified': True, 'mask_sha256': runner.digest(mask),
        'initial_restart_sha256': {n: runner.digest(initial / n) for n in names}}))
    runner.validate_gate(tmp_path, domain, gate)
    (initial / names[0]).write_bytes(b'changed')
    with pytest.raises(ValueError, match='restart changed'):
        runner.validate_gate(tmp_path, domain, gate)
def test_retro_promotion_routes_only_retro_and_blocks_partial_moves(tmp_path, monkeypatch):
    import importlib
    import json
    from pathlib import Path

    import pytest

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    runner = importlib.import_module('run_cnrfc_production')
    marker = tmp_path / 'nwm/status/b120/retro-promotion/promotion.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'status': 'moving'}))
    with pytest.raises(RuntimeError, match='incomplete'):
        runner.publication_roots(tmp_path, 'production_v1', 'retro', 'b120')
    marker.write_text(json.dumps({'status': 'complete'}))
    assert runner.publication_roots(tmp_path, 'production_v1', 'retro', 'b120') == (
        tmp_path / 'nwm/outputs/b120/retro', tmp_path / 'nwm/restarts/b120/retro')
    assert runner.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt', 'b120')[0].name == 'nrt_continuation_v1'
