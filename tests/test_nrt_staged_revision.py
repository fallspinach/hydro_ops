import importlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    return importlib.import_module('benchmark_nrt_staged_revision')


def test_unique_window_owners(tool):
    days = [date(2026, 9, i) for i in (18, 19, 20)]
    assert tool.window_days(days, dict.fromkeys(days, True)) == [date(2026, 9, i) for i in range(18, 22)]
    assert tool.window_days(days, dict.fromkeys(days, False)) == []


def test_prepared_baseline_fail_closed(tool, tmp_path):
    engine = tool.PreparedNrt.__new__(tool.PreparedNrt)
    engine.baseline = tmp_path
    day = date(2026, 9, 18)
    with pytest.raises(ValueError, match='missing or stale'):
        engine.baseline_day(day)
    source = tmp_path / 'source'
    source.write_text('native')
    path = tool.cycle.day_path(tmp_path, day)
    path.parent.mkdir(parents=True)
    path.write_text('prepared')
    record = {'status': 'passed', 'published_identity': tool.cycle.identity(path),
              'inputs': {'files': [tool.cycle.identity(source)], 'assets': []}}
    tool._atomic_json(tool.receipt(path), record)
    assert engine.baseline_day(day)[0] == path
    source.write_text('native changed')
    with pytest.raises(ValueError, match='Source changed'):
        engine.baseline_day(day)


def test_prepared_window_signature(tool, tmp_path, monkeypatch):
    day = date(2026, 9, 18)
    baseline, windows = tmp_path / 'baseline', tmp_path / 'windows/nrt'
    records = []
    for d in (day - timedelta(days=1), day):
        p = tool.cycle.day_path(baseline, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(d))
        r = {'status': 'passed', 'day': str(d), 'sha256': str(d),
             'published_identity': tool.cycle.identity(p)}
        tool._atomic_json(tool.receipt(p), r)
        records.append((p, r))
    prism = []
    for v in ('ppt', 'tmin', 'tmax'):
        p = tmp_path / f'forcing/inputs/oregon_state/prism/an/4km/daily/{v}/2026/09/prism_{v}_us_25m_20260918.nc'
        p.parent.mkdir(parents=True)
        p.write_text(v)
        prism.append(p)
    monkeypatch.setattr(tool.cycle, 'configuration', lambda root: {'reconciliation_writer_profile': 'validated_chunks_reuse_v1'})
    def fake_run(command, **kwargs):
        assert command[command.index('--baseline-archives') + 1:] == [str(p) for p, _ in records]
        p = tool.cycle.day_path(windows, day)
        p.parent.mkdir(parents=True)
        p.write_text('window')
    monkeypatch.setattr(tool.subprocess, 'run', fake_run)
    for key in ('HYDRO_OPS_ARCHIVE_CHUNKS', 'HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS', 'HYDRO_OPS_NRT_REUSE_WINDOWS'):
        monkeypatch.delenv(key, raising=False)
    tool.window_task((tmp_path, tmp_path / 'work', datetime(2026, 9, 26, tzinfo=UTC), baseline, windows, day))
    p = tool.cycle.day_path(windows, day)
    marker = json.loads(p.with_name(p.name + '.reuse.json').read_text())
    assert marker == {'identity': tool.cycle.identity(p),
                      'signature': tool.cycle.window_signature(day, records, prism, 'early', '1:1')}
