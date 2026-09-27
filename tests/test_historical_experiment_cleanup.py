import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
cleanup = importlib.import_module('cleanup_historical_experiments')


def fixture_plan(tmp_path):
    for base in cleanup.ROOTS:
        (tmp_path / base).mkdir(parents=True)
    base = tmp_path / cleanup.ROOTS[0]
    (base / 'old.nc').write_bytes(b'array')
    (base / 'acceptance.json').write_text('{"status":"passed"}')
    (base / 'RESTART.nc').write_bytes(b'keep')
    (base / 'redirect.nc').symlink_to(base / 'old.nc')
    deleted, retained = cleanup.inventory(tmp_path)
    return {'project_root': str(tmp_path), 'reviewed_roots': list(cleanup.ROOTS),
                'mode': 'reviewed_experimental_arrays_only', 'delete': deleted, 'retain': retained}


def test_deletes_only_reviewed_arrays_and_preserves_reports(tmp_path):
    plan = fixture_plan(tmp_path)
    assert len(plan['delete']) == 1
    report = cleanup.execute(tmp_path, plan, tmp_path / 'journal.jsonl', 'mock queue')
    assert report['deleted_files'] == 1
    assert report['retained_reports_verified']
    assert (tmp_path / cleanup.ROOTS[0] / 'RESTART.nc').exists()


def test_changed_array_blocks_all_deletion(tmp_path):
    plan = fixture_plan(tmp_path)
    (tmp_path / plan['delete'][0]['path']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='Changed'):
        cleanup.execute(tmp_path, plan, tmp_path / 'journal.jsonl', '')
    assert not (tmp_path / 'journal.jsonl').exists()


def test_report_change_blocks_deletion(tmp_path):
    plan = fixture_plan(tmp_path)
    (tmp_path / cleanup.ROOTS[0] / 'acceptance.json').write_text('changed')
    with pytest.raises(ValueError, match='Retained'):
        cleanup.execute(tmp_path, plan, tmp_path / 'journal.jsonl', '')


def test_out_of_scope_and_hard_links_retained(tmp_path):
    plan = fixture_plan(tmp_path)
    production = tmp_path / 'forcing/outputs/product.nc'
    production.parent.mkdir(parents=True)
    production.write_bytes(b'keep')
    assert not cleanup.eligible(tmp_path, production)
    linked = tmp_path / cleanup.ROOTS[0] / 'linked.nc'
    linked.hardlink_to(production)
    assert not cleanup.eligible(tmp_path, linked)
    plan['delete'] = [{'path': str(production.relative_to(tmp_path)), 'identity': cleanup.identity(production)}]
    with pytest.raises(ValueError, match='ineligible'):
        cleanup.execute(tmp_path, plan, tmp_path / 'journal.jsonl', '')


def test_live_benchmark_blocks(monkeypatch):
    monkeypatch.setattr(cleanup.subprocess, 'check_output', lambda *a, **k: '123|old-benchmark|RUNNING|x|x\n')
    with pytest.raises(RuntimeError, match='live experiment'):
        cleanup.scheduler_snapshot()
