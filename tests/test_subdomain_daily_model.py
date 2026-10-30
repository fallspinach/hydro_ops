"""Daily model isolation, readiness gates and per-domain scheduling."""
# Model clocks deliberately use timezone-naive UTC.
# ruff: noqa: DTZ001
import json
from datetime import datetime

import pytest

from hydro_ops.wrf_hydro.nrt_cycle import discover_plan
from hydro_ops.wrf_hydro.nrt_schedule import require_daily_acceptance, submit_if_enabled
from hydro_ops.wrf_hydro.production import restart_names, write_json


@pytest.mark.parametrize('domain', ['b120', 'yampa'])
def test_planner_uses_own_domain_and_bootstraps_monthly_checkpoint(tmp_path, domain):
    forcing = tmp_path / f'forcing/outputs/{domain}/nrt/hourly/2026/10/20261030.LDASIN_DOMAIN1'
    forcing.parent.mkdir(parents=True); forcing.touch()
    initial = tmp_path / f'nwm/restarts/{domain}/nrt/2026/10'
    initial.mkdir(parents=True)
    for name in restart_names(datetime(2026, 10, 1)):
        (initial / name).touch()
    write_json(tmp_path / f'nwm/runs/{domain}/nrt/nrt_continuation_v1/interval-passed.json',
               {'passed': True, 'end': '2026-10-29 00:00:00'})
    plan = discover_plan(tmp_path, domain=domain)
    assert plan['start'] == '2026-10-01 00:00:00'
    assert plan['end'] == '2026-10-30 00:00:00'
    assert plan['initial_restart_dir'] == str(initial)
    with pytest.raises(ValueError, match='No CNRFC NRT forcing'):
        discover_plan(tmp_path)


def ready(root, domain):
    config = {'enabled': True, 'rewind_days': 11, 'mpi_ranks': 32,
              'daily_acceptance': f'nwm/status/{domain}/test.json'}
    write_json(root / f'config/{domain}_nrt_daily.json', config)
    write_json(root / config['daily_acceptance'], {'status': 'complete', 'test': True,
               'domain': domain, 'rst_typ': 1, 'daily_checkpoint_continuation': 'passed',
               'mask_sha256': domain})
    write_json(root / f'nwm/static/domains/{domain}/manifest.json', {'mask_sha256': domain})
    write_json(root / f'nwm/status/{domain}/nrt-promotion/promotion.json', {'status': 'complete'})
    return config


def test_readiness_rejects_wrong_domain_mask_or_restart_policy(tmp_path):
    config = ready(tmp_path, 'b120')
    require_daily_acceptance(tmp_path, 'b120', config)
    path = tmp_path / config['daily_acceptance']
    original = json.loads(path.read_text())
    for field, value in [('domain', 'yampa'), ('rst_typ', 0), ('mask_sha256', 'old'), ('test', False)]:
        write_json(path, original | {field: value})
        with pytest.raises(ValueError, match='acceptance'):
            require_daily_acceptance(tmp_path, 'b120', config)


def test_domains_submit_independently_with_own_dependency_and_once_per_day(tmp_path, monkeypatch):
    for domain in ('b120', 'yampa'):
        ready(tmp_path, domain)
    calls = []
    def output(args, **kwargs):
        if args[0] == 'squeue':
            return 'wrfh-cnrfc-nrt-daily-replay-64mpi\n'
        calls.append(args)
        return str(100 + len(calls))
    monkeypatch.setattr('subprocess.check_output', output)
    for domain, dependency in [('b120', '10'), ('yampa', '20')]:
        record = submit_if_enabled(tmp_path, [dependency], domain)
        assert record['status'] == 'submitted'
        assert '--ntasks=32' in record['command']
        assert '--dependency=afterok:' + dependency in record['command']
        assert record['command'][-2:] == ['--domain', domain]
        assert submit_if_enabled(tmp_path, [dependency], domain)['status'] == 'already_submitted'
    assert len(calls) == 2
