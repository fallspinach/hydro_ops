import importlib.util
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_forcing_temporal_summary import chunk


@pytest.mark.parametrize('returncode', [0, 1])
def test_first_refresh_creates_status_directory(tmp_path, monkeypatch, returncode):
    script = Path(__file__).resolve().parents[1] / 'bin/refresh_nrt_summaries.py'
    spec = importlib.util.spec_from_file_location('summary_launcher', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'bin/refresh_nrt_summaries.py'))
    hourly = tmp_path / 'forcing/outputs/conus/nrt/hourly'
    chunk(hourly, date(2026, 9, 20))
    chunk(hourly, date(2026, 9, 21))
    state = tmp_path / 'forcing/status/nrt-summaries/latest.json'
    assert not state.parent.exists()

    def run(command, **kwargs):
        assert json.loads(state.read_text())['status'] == 'running'
        assert command[command.index('--end') + 1] == '2026-09-20'
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == returncode
    assert json.loads(state.read_text())['status'] == ('passed' if returncode == 0 else 'failed')
