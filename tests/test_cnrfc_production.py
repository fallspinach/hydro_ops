import importlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from netCDF4 import Dataset


def test_annual_and_terminal_windows(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('run_cnrfc_production')
    assert tool.simulation_window(1979)[0] == datetime(1979, 1, 2)
    start, end = tool.simulation_window(1984)
    assert (end - start).days == 366
    start, end = tool.simulation_window(2026, '2026-03-02')
    windows = list(tool.months(start, end))
    assert len(windows) == 3
    assert windows[-1] == (datetime(2026, 3, 1), datetime(2026, 3, 2))
    for invalid in ('2026-01-01', '2027-01-02', '2025-12-31'):
        with pytest.raises(ValueError):
            tool.simulation_window(2026, invalid)


def test_continuation_submission_chain(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('submit_cnrfc_continuation')
    monkeypatch.setattr(tool, '__file__', str(tmp_path / 'bin/submit_cnrfc_continuation.py'))
    monkeypatch.setattr('sys.argv', ['submit', '--after-job', '900'])
    commands = []

    def submit(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=str(900 + len(commands)))

    monkeypatch.setattr(tool.subprocess, 'run', submit)
    tool.main()
    report = json.loads((tmp_path / 'nwm/runs/cnrfc/retro/recovery_record_stack_v1/'
                         'continuation-1982-20260302.json').read_text())
    assert report['status'] == 'submitted'
    assert len(commands) == 45
    for index, command in enumerate(commands):
        assert f'--dependency=afterok:{900 + index}' in command
    assert '--end-date' not in commands[-2]
    assert commands[-1][-2:] == ['--end-date', '2026-03-02']
    with pytest.raises(RuntimeError, match='manifest exists'):
        tool.main()


def test_daily_channel_oracle_and_coverage(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('run_cnrfc_production')
    start = datetime(1979, 1, 2)
    hourly = []
    for hour in range(1, 25):
        path = tmp_path / ((start + timedelta(hours=hour)).strftime('%Y%m%d%H') + '.CHRTOUT_DOMAIN1')
        with Dataset(path, 'w') as data:
            data.createDimension('time', 1)
            data.createDimension('feature_id', 2)
            data.createVariable('streamflow', 'f4', ('time', 'feature_id'))[:] = hour
        hourly.append(path)
    daily = tmp_path / '19790102.CHRTOUT_DOMAIN1.daily'
    with Dataset(daily, 'w') as data:
        for name, size in [('time', 1), ('bounds', 2), ('feature_id', 2)]:
            data.createDimension(name, size)
        data.createVariable('time', 'f8', ('time',)).units = 'hours since 1979-01-02 00:00:00'
        data.createVariable('time_bounds', 'f8', ('time', 'bounds'))[:] = [[0, 24]]
        flow = data.createVariable('streamflow', 'f4', ('time', 'feature_id'))
        flow[:] = 12.5
        flow.cell_methods = 'time: mean'
    assert tool.daily_channels(tmp_path, start, start + timedelta(days=1), hourly) == [daily]
    with Dataset(daily, 'r+') as data:
        data['streamflow'][:] = 99
    with pytest.raises(AssertionError):
        tool.daily_channels(tmp_path, start, start + timedelta(days=1), hourly)
    with pytest.raises(ValueError, match='coverage'):
        tool.daily_channels(tmp_path, start, start + timedelta(days=2), hourly)
