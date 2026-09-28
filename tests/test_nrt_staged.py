import json
import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing import nrt_staged as staged


def test_recent_worker_uses_selected_limit_without_reservation():
    path = Path(__file__).resolve().parents[1] / 'slurm/converge_nwm_forcing_cycle.py'
    tree = ast.parse(path.read_text())
    command = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'command' for t in node.targets)
                   and 'recent-nrt-gfs' in ast.unparse(node.value))
    text = ast.unparse(command.value)
    assert '--time={revision_limit}' in text
    assert '--time=48:00:00' not in text
    assert '--reservation' not in text


def test_switch_does_not_invalidate_baselines():
    old = {'assembly_workers': 4, 'enabled': True}
    added = {**old, 'revision_pipeline': 'staged_v1', 'revision_baseline_workers': 4,
             'revision_window_workers': 4, 'revision_publication_workers': 2}
    assert cycle.baseline_configuration(added) == cycle.baseline_configuration(old)
    assert staged.worker_counts(added) == (4, 4, 2)
    with pytest.raises(ValueError):
        staged.worker_counts({**added, 'revision_window_workers': 5})


def test_dependency_ownership():
    days = [date(2026, 9, d) for d in (18, 19, 20)]
    flags = dict.fromkeys(days, True)
    assert staged.dependencies(days, flags) == [date(2026, 9, d) for d in range(17, 22)]
    assert staged.required_windows(days, flags) == [date(2026, 9, d) for d in range(18, 22)]


@pytest.mark.parametrize('dirty', [False, True])
def test_skip_unchanged_and_deduplicate_windows(tmp_path, monkeypatch, dirty):
    monkeypatch.setenv('SLURM_CPUS_PER_TASK', '128')
    monkeypatch.delenv('HYDRO_OPS_NRT_REUSE_WINDOWS', raising=False)
    days = [date(2026, 9, d) for d in (18, 19)]
    flags = dict.fromkeys(days, True)
    prism = tmp_path / 'prism'
    prism.write_text('prism')
    parent = SimpleNamespace(root=tmp_path, work=tmp_path, baseline=tmp_path / 'baseline',
                             output=tmp_path / 'nrt', as_of=None,
                             config={'reconciliation_writer_profile': 'validated_chunks_reuse_v1'},
                             prism_paths=lambda d: [prism], prism_cache=lambda: None)
    prepared = {}
    for d in staged.dependencies(days, flags):
        p = cycle.day_path(parent.baseline, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(d))
        r = {'status': 'passed', 'day': str(d), 'sha256': str(d), 'input_fingerprint': str(d),
             'published_identity': cycle.identity(p), 'inputs': {'files': [], 'assets': []}}
        cycle._atomic_json(p.with_name(p.name + '.nrt-receipt.json'), r)
        prepared[d] = p, r
    for d in days:
        p = cycle.day_path(parent.output, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('accepted')
        deps = [prepared[x] for x in staged.dependencies([d], flags)]
        r = {'status': 'passed', 'published_identity': cycle.identity(p),
             'input_fingerprint': cycle.fingerprint(cycle.publication_inputs(deps, [prism])),
             'gfs_hours': 0, 'prism_constrained': True}
        if dirty and d == days[0]:
            r['input_fingerprint'] = 'stale'
        cycle._atomic_json(p.with_name(p.name + '.nrt-receipt.json'), r)
    calls = []
    def fake_pool(parent, work, windows, mode, workers, function, tasks, progress):
        calls.append((mode, list(tasks)))
        return [{'day': str(d), 'status': 'unchanged' if mode == 'baseline' else 'published',
                 'gfs_hours': 0, 'prism_constrained': True} for d in tasks]
    monkeypatch.setattr(staged, 'run_pool', fake_pool)
    results, timing = staged.run_staged_days(parent, days)
    assert calls[1] == ('window', [days[0], days[0] + timedelta(days=1)] if dirty else [])
    assert calls[2] == ('publish', [days[0]] if dirty else [])
    assert [r['status'] for r in results] == (['published', 'unchanged'] if dirty else ['unchanged'] * 2)
    assert timing['workers'] == (4, 4, 2)


def test_prepared_inputs_fail_closed(tmp_path):
    d = date(2026, 9, 18)
    with pytest.raises(ValueError, match='missing or stale'):
        staged.prepared_baseline(tmp_path, d)
    path = cycle.day_path(tmp_path, d)
    path.parent.mkdir(parents=True)
    path.write_text('baseline')
    native = tmp_path / 'native'
    native.write_text('source')
    record = {'status': 'passed', 'published_identity': cycle.identity(path),
              'inputs': {'files': [cycle.identity(native)], 'assets': []}}
    cycle._atomic_json(path.with_name(path.name + '.nrt-receipt.json'), record)
    assert staged.prepared_baseline(tmp_path, d)[0] == path
    native.write_text('changed')
    with pytest.raises(ValueError, match='Source changed'):
        staged.prepared_baseline(tmp_path, d)


def test_cycle_dispatch_staged_under_existing_lock(tmp_path, monkeypatch):
    class Engine:
        def __init__(self, *args, **kwargs):
            self.output, self.layout = tmp_path / 'output', None
            self.config = {'revision_pipeline': 'staged_v1'}
        def produce_day(self, day):
            pytest.fail('Serial path must not execute')
    monkeypatch.setattr(cycle, 'RecentNrt', Engine)
    monkeypatch.setattr(cycle, 'replacement_backlog', lambda *args: [])
    def fake(engine, days, progress):
        progress({'phase': 'publish'})
        return [{'day': str(d), 'status': 'unchanged'} for d in days], {'workers': [4, 4, 2]}
    monkeypatch.setattr(staged, 'run_staged_days', fake)
    d = date(2026, 9, 18)
    report = cycle.run_cycle(tmp_path, tmp_path / 'work', d, d, datetime.now(UTC), state_root=tmp_path / 'state')
    assert report['status'] == 'passed'
    assert report['revision_pipeline'] == 'staged_v1'
    assert json.loads((tmp_path / 'state/latest.json').read_text())['staged_progress']['phase'] == 'publish'
