import importlib
import json
from pathlib import Path

import pytest


def test_isolated_nrt_paths_do_not_change_retro_or_old_cnrfc(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('run_cnrfc_production')
    for domain in ('cnrfc_hu18_v1', 'yampa', 'b120'):
        outputs, restarts = module.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt', domain)
        assert outputs == tmp_path / 'nwm/outputs' / domain / 'nrt/nrt_continuation_v1'
        assert restarts == tmp_path / 'nwm/restarts' / domain / 'nrt/nrt_continuation_v1'
        with pytest.raises(ValueError, match='Unsupported'):
            module.publication_roots(tmp_path, 'production_v1', 'nrt', domain)
    assert module.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt')[0] == tmp_path / 'nwm/outputs/cnrfc/nrt'
    with pytest.raises(ValueError, match='Unsupported'):
        module.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt', 'ucrb')


def test_handoff_gate_pins_domain_plan_mask_and_success(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('run_subdomain_nrt_catchup')
    path = tmp_path / 'acceptance.json'
    record = {'status': 'passed', 'domain': 'yampa', 'plan_sha256': 'plan', 'mask_sha256': 'mask',
              'nrt_handoff_verified': True, 'spinup_warm_start_verified': True,
              'all_archived_records_verified': True}
    path.write_text(json.dumps(record))
    assert module.verified_gate(path, 'yampa', 'plan', 'mask') == record
    for field, bad in (('domain', 'cnrfc_hu18_v1'), ('status', 'failed'),
                       ('plan_sha256', 'changed'), ('mask_sha256', 'changed'),
                       ('nrt_handoff_verified', False), ('all_archived_records_verified', False)):
        path.write_text(json.dumps(record | {field: bad}))
        with pytest.raises(ValueError, match='handoff'):
            module.verified_gate(path, 'yampa', 'plan', 'mask')


def test_yampa_promotion_routes_future_runs_and_blocks_partial_moves(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('run_cnrfc_production')
    marker = tmp_path / 'nwm/status/yampa/nrt-promotion/promotion.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'status': 'moving'}))
    with pytest.raises(RuntimeError, match='incomplete'):
        module.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt', 'yampa')
    marker.write_text(json.dumps({'status': 'complete'}))
    assert module.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt', 'yampa') == (
        tmp_path / 'nwm/outputs/yampa/nrt', tmp_path / 'nwm/restarts/yampa/nrt')
    assert module.publication_roots(tmp_path, 'production_v1', 'retro', 'yampa')[0].name == 'production_v1'


def test_isolated_nrt_refuses_old_gate_without_handoff_or_snapshot(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('run_cnrfc_production')
    from datetime import datetime
    gate = tmp_path / 'old.json'
    gate.write_text(json.dumps({'status': 'passed', 'domain': 'yampa', 'spinup_warm_start_verified': True}))
    with pytest.raises(ValueError, match='handoff'):
        module.run(tmp_path, 2026, campaign='nrt_continuation_v1', stream='nrt',
            start_date='2026-04-18', end_date='2026-04-20', initial_restart_dir=tmp_path,
            archive_gate=gate, domain='yampa', continuation_end=datetime.fromisoformat('2026-04-20'))
    assert not (tmp_path / 'nwm').exists()
