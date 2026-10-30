from pathlib import Path

import pytest

from hydro_ops.forcing import subdomain_sync as sync


def test_parallel_branches_and_idempotent_submission(tmp_path, monkeypatch):
    calls = []
    def submit(root, domain, parent, partition, account):
        calls.append((domain, parent))
        return str(100 + len(calls))
    monkeypatch.setattr(sync, 'submit_domain', submit)
    args = {'cnrfc_job': '10', 'summary_job': '20', 'cycle_id': '30', 'partition': 'shared-128'}
    expected = {'b120': '101', 'ucrb': '102', 'yampa': '103'}
    assert sync.submit_daily_branches(tmp_path, **args) == expected
    assert calls == [('b120', '10'), ('ucrb', '20'), ('yampa', '102')]
    assert sync.submit_daily_branches(tmp_path, **args) == expected
    assert len(calls) == 3
    with pytest.raises(ValueError, match='different parent'):
        sync.submit_daily_branches(tmp_path, **(args | {'summary_job': '21'}))


def test_parent_success_prior_writer_completion_and_resources(tmp_path, monkeypatch):
    calls = []
    def output(command, **kwargs):
        calls.append(command)
        return ('40|forcing-b120-subdomain-nrt-refresh\n41|wrfh-b120-model\n'
                '42|forcing-yampa-daily-nrt-sync-all\n') if command[0] == 'squeue' else '50\n'
    monkeypatch.setattr(sync.subprocess, 'check_output', output)
    assert sync.submit_domain(tmp_path, 'b120', '10', 'shared-128') == '50'
    assert '--dependency=afterok:10,afterany:40' in calls[-1]
    assert '--cpus-per-task=32' in calls[-1]
    assert calls[-1][-4:] == ['--domain', 'b120', '--workers', '8']
    assert not any('reservation' in x for x in calls[-1])


def test_uncertain_submission_cannot_duplicate_job(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError('unknown sbatch outcome')
    monkeypatch.setattr(sync, 'submit_domain', fail)
    args = {'cnrfc_job': '10', 'summary_job': '20', 'cycle_id': '30', 'partition': 'shared-128'}
    with pytest.raises(RuntimeError, match='unknown'):
        sync.submit_daily_branches(tmp_path, **args)
    with pytest.raises(RuntimeError, match='uncertain'):
        sync.submit_daily_branches(tmp_path, **args)


def test_cycle_hook_is_daily_nrt_only():
    import ast
    source = Path(__file__).resolve().parents[1] / 'slurm/converge_nwm_forcing_cycle.py'
    tree = ast.parse(source.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    hook = next(n for n in fn.body if isinstance(n, ast.If)
                and 'submit_daily_branches' in ast.unparse(n))
    assert ast.unparse(hook.test) == "state['stream'] == 'nrt' and state['cycle'] == 'daily'"
