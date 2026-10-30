"""Configured once-per-UTC-day model submission after successful forcing dependencies."""
import fcntl
import json
import re
import subprocess
from datetime import UTC, datetime

from hydro_ops.wrf_hydro.production import write_json


def command(root, dependencies, rewind_days, domain='cnrfc', mpi_ranks=64):
    if domain not in ('cnrfc', 'b120', 'yampa') or mpi_ranks not in (32, 64):
        raise ValueError('Unsupported daily model domain/rank selection')
    if not dependencies or not all(re.fullmatch(r'\d+', str(j)) for j in dependencies):
        raise ValueError('Explicit successful forcing/subset dependencies are required')
    if not isinstance(rewind_days, int) or rewind_days < 1:
        raise ValueError('Positive rewind required')
    args = ['sbatch', '--parsable', '--dependency=afterok:' + ':'.join(dict.fromkeys(map(str, dependencies))),
            f'--job-name=wrfh-{domain}-nrt-daily-replay-{mpi_ranks}mpi', f'--chdir={root}']
    if domain != 'cnrfc':
        args += [f'--ntasks={mpi_ranks}', '--kill-on-invalid-dep=yes',
                 f'--output={root}/nwm/logs/{domain}-daily-cycle-%j.out']
    args += [
            str(root / 'slurm/run_cnrfc_daily_cycle.sh'), '--rewind-days', str(rewind_days),
            '--snapshot-forcing']
    if domain != 'cnrfc':
        args += ['--domain', domain]
    return args


def require_daily_acceptance(root, domain, config):
    if domain not in ('b120', 'yampa'):
        raise ValueError('Unsupported new daily model domain')
    report = json.loads((root / config['daily_acceptance']).read_text())
    manifest = json.loads((root / f'nwm/static/domains/{domain}/manifest.json').read_text())
    promotion = json.loads((root / f'nwm/status/{domain}/nrt-promotion/promotion.json').read_text())
    if (report.get('status') != 'complete' or not report.get('test')
            or report.get('domain') != domain or report.get('rst_typ') != 1
            or report.get('daily_checkpoint_continuation') != 'passed'
            or report.get('mask_sha256') != manifest['mask_sha256']
            or promotion.get('status') != 'complete'):
        raise ValueError('Domain-specific daily replay acceptance/promotion missing or changed')


def submit_if_enabled(root, dependencies, domain='cnrfc'):
    if domain not in ('cnrfc', 'b120', 'yampa'):
        raise ValueError('Unsupported daily model domain')
    if domain == 'cnrfc':
        from hydro_ops.cnrfc_domain import migration_state
        if migration_state(root).get('blocked'):
            return {'status': 'disabled_cnrfc_grid_migration'}
    config = json.loads((root / f'config/{domain}_nrt_daily.json').read_text())
    if not config.get('enabled', False):
        return {'status': 'disabled_pending_acceptance'}
    if domain != 'cnrfc':
        require_daily_acceptance(root, domain, config)
    state = root / f'nwm/status/{domain}/nrt-daily'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        day = datetime.now(UTC).strftime('%Y%m%d')
        record_path = state / f'submission-{day}.json'
        if record_path.exists():
            return {'status': 'already_submitted', 'record': str(record_path)}
        queued = subprocess.check_output(['squeue', '--me', '-h', '-o', '%j'], text=True)
        if any(f'wrfh-{domain}' in name for name in queued.splitlines()):
            return {'status': 'deferred_model_job_active'}
        args = command(root, dependencies, config.get('rewind_days', 11), domain,
                       config.get('mpi_ranks', 64 if domain == 'cnrfc' else 32))
        report = {'status': 'submitting', 'command': args}
        write_json(record_path, report)
        try:
            response = subprocess.check_output(args, cwd=root, text=True).strip().split(';')[0]
            if not response.isdecimal():
                raise ValueError('Unexpected sbatch response')
            report.update(status='submitted', job_id=response)
        except BaseException as error:
            report.update(status='submission_uncertain', error=str(error))
            raise
        finally:
            write_json(record_path, report)
        return report
