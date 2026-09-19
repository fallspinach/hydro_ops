"""Submit isolated real-data reliability tests; never install cron automatically."""
import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime

from hydro_ops.config import load_settings
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    settings = load_settings()
    root = settings.project_root
    campaign = settings.work_root / f'nrt-reliability-{datetime.now(UTC):%Y%m%dT%H%M%S}'
    tests = {
        'temperature': ['bin/benchmark_nrt_revisions.py', '--scenario', 'temperature'],
        'nldas-three-days': ['bin/benchmark_nrt_revisions.py', '--scenario', 'nldas', '--arrival-days', '3'],
        'worker-sequence': ['bin/test_nrt_reliability_sequence.py'],
    }
    report = {'campaign': str(campaign), 'status': 'planned', 'jobs': {}, 'cron_installed': False,
              'remaining_gates': ['live scheduler/source-refresh coordination', 'optimized forcing model restart smoke']}
    if args.submit:
        campaign.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
               PYTHONPATH=str(root / 'src'), PATH=str(sys.executable.rsplit('/', 1)[0]) + os.pathsep + env['PATH'])
    for key in list(env):
        if key.startswith('HYDRO_OPS_BENCH_') or key in ('HYDRO_OPS_NRT_WINDOW_CACHE', 'HYDRO_OPS_PRISM_DATA_DIR'):
            env.pop(key)
    for name, arguments in tests.items():
        command = ['sbatch', '--parsable', f'--partition={settings.slurm_partition}', '--nodes=1', '--ntasks=1',
                   '--cpus-per-task=64', '--tmp=240000', '--time=24:00:00', f'--job-name=nrt-reliability-{name}',
                   f'--chdir={root}', f'--output={settings.log_root}/nrt-reliability-{name}-%j.out']
        if settings.slurm_account:
            command.append(f'--account={settings.slurm_account}')
        command += ['--wrap', shlex.join([sys.executable, '-u', *arguments, '--campaign', str(campaign / name)])]
        report['jobs'][name] = {'command': command}
        if args.submit:
            _atomic_json(campaign / 'submission.json', report)
            report['jobs'][name]['job'] = subprocess.check_output(command, env=env, text=True).strip().split(';')[0]
            _atomic_json(campaign / 'submission.json', report)
    if args.submit:
        report['status'] = 'submitted'
        _atomic_json(campaign / 'submission.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
