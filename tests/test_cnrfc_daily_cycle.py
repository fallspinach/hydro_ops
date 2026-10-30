# Model clocks deliberately use timezone-naive UTC.
# ruff: noqa: DTZ001
import ast
import importlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hydro_ops.wrf_hydro.nrt_cycle import choose_start, publish_transaction, replace_link
from hydro_ops.wrf_hydro.nrt_schedule import command, submit_if_enabled


def test_corrected_replay_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('run_cnrfc_daily_cycle')
    valid = {'status': 'complete', 'test': True,
             'daily_checkpoint_continuation': 'passed', 'rst_typ': 1}
    tool.require_corrected_replay(valid)
    for key, value in [('status', 'failed'), ('test', False),
                       ('daily_checkpoint_continuation', 'failed'), ('rst_typ', 0)]:
        with pytest.raises(ValueError, match='acceptance gate'):
            tool.require_corrected_replay(valid | {key: value})
    with pytest.raises(ValueError):
        tool.require_corrected_replay({})


def test_future_coupled_warm_start_policy():
    root = Path(__file__).resolve().parents[1]
    for path in ['bin/run_cnrfc_production.py', 'src/hydro_ops/wrf_hydro/production.py']:
        tree = ast.parse((root / path).read_text())
        settings = [value.value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and key.value == 'rst_typ'
                    and isinstance(value, ast.Constant)]
        assert settings and set(settings) == {1}


def test_seasonal_streamflow_metric(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('test_cnrfc_seasonal_restarts')
    assert tool.flow_metrics([1., 3.], [2., 1.]) == {
        'max_abs_m3_s': 2., 'mean_abs_m3_s': 1.5, 'mean_signed_m3_s': -.5}


def test_rewind_and_bootstrap():
    end = datetime(2026, 10, 10)
    monthly = [datetime(2026, 9, 1), datetime(2026, 10, 1)]
    assert choose_start(end, monthly) == monthly[0]
    daily = [end-timedelta(days=i) for i in range(31)]
    assert choose_start(end, daily) == datetime(2026, 9, 29)
    # A long outage must not skip the gap between the model and new forcing.
    assert choose_start(end, monthly, published_end=datetime(2026, 9, 10)) == monthly[0]
    with pytest.raises(ValueError, match='shorten'):
        choose_start(end, monthly, published_end=end+timedelta(days=1))
    with pytest.raises(ValueError, match='No checkpoint'):
        choose_start(end, [datetime(2026, 10, 1)])


def test_transaction_success_and_recoverable_backup(tmp_path):
    old, new = tmp_path/'canonical', tmp_path/'candidate'
    old.write_text('old'); new.write_text('new')
    journal = tmp_path/'tx/report.json'
    report = publish_transaction([(new, old)], journal)
    assert report['status'] == 'complete'
    assert old.read_text() == 'new'
    assert (tmp_path/'tx/previous/0').read_text() == 'old'
    with pytest.raises(ValueError, match='already exists'):
        publish_transaction([(new, old)], journal)


def test_transaction_rollback(tmp_path):
    a, b, c, d = [tmp_path/name for name in ['a', 'b', 'c', 'd']]
    a.write_text('new-a'); b.write_text('old-b'); c.write_text('new-c')
    calls = []

    def fail_after_one(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise RuntimeError('injected failure')
        replace_link(source, target)

    journal = tmp_path/'tx/report.json'
    with pytest.raises(RuntimeError, match='injected'):
        publish_transaction([(a, b), (c, d)], journal, activate=fail_after_one)
    assert b.read_text() == 'old-b'
    assert not d.exists()
    assert json.loads(journal.read_text())['status'] == 'rolled_back'


def test_hook_dependencies_and_disabled_gate(tmp_path, monkeypatch):
    args = command(tmp_path, ['123', '124', '123'], 11)
    assert '--dependency=afterok:123:124' in args
    assert '--snapshot-forcing' in args
    assert not any('reservation' in x for x in args)
    with pytest.raises(ValueError):
        command(tmp_path, [], 11)
    config = tmp_path/'config/cnrfc_nrt_daily.json'
    config.parent.mkdir(); config.write_text('{"enabled": false}')

    def unexpected(*args, **kwargs):
        raise AssertionError('Disabled hook must not query or submit jobs')

    monkeypatch.setattr('subprocess.check_output', unexpected)
    assert submit_if_enabled(tmp_path, ['123'])['status'] == 'disabled_pending_acceptance'


def test_enabled_hook_submits_once_and_defers_active_job(tmp_path, monkeypatch):
    config = tmp_path/'config/cnrfc_nrt_daily.json'
    config.parent.mkdir()
    config.write_text('{"enabled": true, "rewind_days": 11}')
    calls = []

    def active(args, **kwargs):
        assert args[0] == 'squeue'
        return 'wrfh-cnrfc-nrt-daily-replay-64mpi\n'

    monkeypatch.setattr('subprocess.check_output', active)
    assert submit_if_enabled(tmp_path, ['123'])['status'] == 'deferred_model_job_active'

    def submit(args, **kwargs):
        calls.append(args)
        return '' if args[0] == 'squeue' else '456;cluster\n'

    monkeypatch.setattr('subprocess.check_output', submit)
    result = submit_if_enabled(tmp_path, ['123', '124', '125'])
    assert result['job_id'] == '456' and result['status'] == 'submitted'
    assert '--dependency=afterok:123:124:125' in result['command']
    assert '--snapshot-forcing' in result['command']
    assert submit_if_enabled(tmp_path, ['126'])['status'] == 'already_submitted'
    assert len(calls) == 2
