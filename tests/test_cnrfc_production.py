# Model clocks deliberately use timezone-naive UTC.
# ruff: noqa: DTZ001
import importlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from netCDF4 import Dataset


def test_promoted_paths_and_incomplete_gate(tmp_path, monkeypatch):
    import json

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('run_cnrfc_production')
    campaign = 'recovery_record_stack_v1'
    assert tool.publication_roots(tmp_path, campaign)[0].name == campaign
    marker = tmp_path / 'nwm/status/cnrfc/retro-promotion/promotion.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'status': 'moving'}))
    with pytest.raises(RuntimeError, match='incomplete'):
        tool.publication_roots(tmp_path, campaign)
    marker.write_text(json.dumps({'status': 'complete'}))
    assert tool.publication_roots(tmp_path, campaign) == (
        tmp_path / 'nwm/outputs/cnrfc/retro', tmp_path / 'nwm/restarts/cnrfc/retro')
    assert tool.publication_roots(tmp_path, 'production_1979_1981_v1')[0].name == 'production_1979_1981_v1'


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


def test_nrt_window_paths_and_required_inputs(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('run_cnrfc_production')
    start, end = tool.simulation_window(2026, '2026-03-04', '2026-03-02')
    assert (end - start).days == 2
    assert list(tool.months(start, end)) == [(start, end)]
    for value in ('2025-03-02', '2026-03-04', '2026-03-05'):
        with pytest.raises(ValueError):
            tool.simulation_window(2026, '2026-03-04', value)
    assert tool.publication_roots(tmp_path, 'nrt_continuation_v1', 'nrt') == (
        tmp_path / 'nwm/outputs/cnrfc/nrt', tmp_path / 'nwm/restarts/cnrfc/nrt')
    outputs, restarts = tool.publication_roots(tmp_path, 'nrt_warmstart_test_v1', 'nrt')
    assert outputs == tmp_path / 'nwm/outputs/cnrfc/nrt/tests/nrt_warmstart_test_v1'
    assert restarts == tmp_path / 'nwm/restarts/cnrfc/nrt/tests/nrt_warmstart_test_v1'
    for campaign, stream in [('nrt_continuation_v1', 'retro'), ('recovery_record_stack_v1', 'nrt')]:
        with pytest.raises(ValueError):
            tool.publication_roots(tmp_path, campaign, stream)
    with pytest.raises(ValueError, match='explicit'):
        tool.run(tmp_path, 2026, campaign='nrt_warmstart_test_v1', stream='nrt')
    with pytest.raises(ValueError, match='archive acceptance'):
        tool.run(tmp_path, 2026, campaign='nrt_warmstart_test_v1', stream='nrt',
                 start_date='2026-03-02', end_date='2026-03-04', initial_restart_dir=tmp_path)
    assert not (tmp_path / 'nwm').exists()


def test_forcing_identity_detects_replacement(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('run_cnrfc_production')
    path = tmp_path / 'forcing'
    path.write_bytes(b'original')
    identity = tool.forcing_identity(path)
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'original')
    replacement.replace(path)
    assert tool.forcing_identity(path) != identity


def test_nrt_submission_isolated_and_once(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('submit_cnrfc_nrt_test')
    monkeypatch.setattr(tool, '__file__', str(tmp_path / 'bin/submit_cnrfc_nrt_test.py'))
    gate = tmp_path / ('nwm/outputs/cnrfc/retro/tests/'
                       'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
    gate.parent.mkdir(parents=True)
    gate.write_text(json.dumps({'status': 'passed'}))
    commands = []

    def submit(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout='12345')

    monkeypatch.setattr(tool.subprocess, 'run', submit)
    tool.main()
    command = commands[0]
    assert '--time=02:00:00' in command
    assert command[command.index('--stream') + 1] == 'nrt'
    assert command[command.index('--campaign') + 1] == 'nrt_warmstart_test_v1'
    assert not any('reservation' in arg for arg in command)
    report = json.loads((tmp_path / 'nwm/runs/cnrfc/nrt/nrt_warmstart_test_v1/submission.json').read_text())
    assert report['job_id'] == 12345
    with pytest.raises(RuntimeError, match='Submission record exists'):
        tool.main()
    assert len(commands) == 1


def test_catchup_command_and_acceptance_gate(tmp_path, monkeypatch):
    import json

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('submit_cnrfc_nrt_catchup')
    command = tool.command_for(tmp_path, '2026-10-10')
    assert '--time=06:00:00' in command
    assert command[command.index('--campaign') + 1] == 'nrt_continuation_v1'
    assert command[command.index('--end-date') + 1] == '2026-10-10'
    assert not any('reservation' in arg for arg in command)
    path = tmp_path / 'nwm/runs/cnrfc/nrt/nrt_warmstart_test_v1/20260302-20260304/accepted.json'
    path.parent.mkdir(parents=True)
    record = {'status': 'passed', 'passed': True, 'stream': 'nrt',
              'start': '2026-03-02 00:00:00', 'end': '2026-03-04 00:00:00',
              'hourly_channel_records': 48, 'daily_land_files': 2, 'daily_channel_files': 2,
              'daily_streamflow_oracle': 'first day passed'}
    path.write_text(json.dumps(record))
    assert tool.require_test(tmp_path) == path
    record['hourly_channel_records'] = 47
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='acceptance'):
        tool.require_test(tmp_path)


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
