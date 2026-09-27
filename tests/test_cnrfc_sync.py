import importlib
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_ops.forcing import cnrfc_sync


@pytest.mark.parametrize('cycle,frequency', [('six-hourly', 'all'), ('daily', 'hourly')])
def test_latest_cycle_followup_scope(cycle, frequency):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'bin/update_nwm_forcing.py').read_text())
    block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                 and 'cnrfc_hourly_job_id' in ast.unparse(node))
    calls = []
    plan = {}
    namespace = dict(args=SimpleNamespace(cycle=cycle), plan=plan, job='123',
                     settings=SimpleNamespace(project_root=root, slurm_partition='compute', slurm_account=''),
                     submit_cnrfc=lambda *a, **kw: calls.append(kw) or '456')
    exec(compile(ast.Module(body=block.body, type_ignores=[]), '<followup>', 'exec'), namespace)
    assert calls == [dict(stream='nrt', frequency=frequency, after='123', partition='compute', account='')]
    assert plan['cnrfc_hourly_job_id'] == '456'


@pytest.mark.parametrize('cycle,stream,summary,expected', [
    ('daily', 'nrt', '234', 'all'),
    ('monthly-retro', 'retro', None, 'retro'),
])
def test_completed_cycle_followup_scope(cycle, stream, summary, expected):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'slurm/converge_nwm_forcing_cycle.py').read_text())
    block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                 and 'cnrfc_sync_job_id' in ast.unparse(node))
    calls = []
    state = dict(cycle=cycle, stream=stream, summary_refresh_job_id=summary, partition='compute')
    namespace = dict(state=state, project=root, os=SimpleNamespace(environ={'SLURM_JOB_ID': '123'}),
                     submit_cnrfc=lambda *a, **kw: calls.append(kw) or '456')
    exec(compile(ast.Module(body=block.body, type_ignores=[]), '<followup>', 'exec'), namespace)
    assert calls == [dict(stream=expected, frequency='all', after=summary or '123',
                         partition='compute', account='')]
    assert state['cnrfc_sync_job_id'] == '456'


def test_serialized_submission(tmp_path, monkeypatch):
    monkeypatch.setattr('hydro_ops.config.load_settings', lambda: SimpleNamespace(slurm_partition='shared-128', slurm_account=''))
    calls = []
    def fake(args, **kwargs):
        calls.append(args)
        return '123|forcing-cnrfc-sync-nrt-hourly\n42|unrelated\n' if args[0] == 'squeue' else '456\n'
    monkeypatch.setattr(cnrfc_sync.subprocess, 'check_output', fake)
    assert cnrfc_sync.submit(tmp_path, after='234') == '456'
    assert '--dependency=afterany:123:234' in calls[-1]
    assert (tmp_path / 'forcing/status/cnrfc/submission-456.json').exists()


def test_incremental_skip_is_metadata_only(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_cnrfc_forcing')
    source, target, mask = [tmp_path / n for n in ('source', 'target', 'mask')]
    source.write_bytes(b'original'); mask.write_bytes(b'mask')
    def subset(src, dst, masks):
        dst.write_bytes(src.read_bytes())
        return {'status': 'passed', 'source': str(src.resolve())}
    monkeypatch.setattr(tool, 'subset', subset)
    task = (source, target, mask, tool.digest(mask), tmp_path, tool.identity(source))
    assert tool.process(task)['status'] == 'published'
    original = tool.digest
    def digest(path):
        assert path != target, 'Unchanged output must not be rehashed'
        return original(path)
    monkeypatch.setattr(tool, 'digest', digest)
    assert tool.process(task)['status'] == 'skipped'
    source.write_bytes(b'new value')
    monkeypatch.setattr(tool, 'digest', original)
    assert tool.process_retry(task)['status'] == 'published'
    assert target.read_bytes() == b'new value'


def test_stream_resolution_filters(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_cnrfc_forcing')
    for stream in ('nrt', 'retro'):
        for suffix in ('hourly/2026/09/20260925.LDASIN_DOMAIN1',
                       'daily/2026/09/20260925.LDASIN_DOMAIN1.daily',
                       'monthly/2026/202609.LDASIN_DOMAIN1.monthly'):
            p = tmp_path / stream / suffix
            p.parent.mkdir(parents=True, exist_ok=True); p.touch()
    assert len(tool.discover(tmp_path)) == 6
    assert len(tool.discover(tmp_path, ('nrt',), ('hourly',))) == 1
