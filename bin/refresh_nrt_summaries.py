"""Daily dependent summary refresh; unchanged inputs are skipped by signature."""
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'forcing/outputs/conus/nrt'
    paths = sorted((output / 'hourly').glob('*/*/*.LDASIN_DOMAIN1'))
    if not paths:
        raise ValueError('No NRT hourly archive')
    first = date.fromisoformat(f'{paths[0].name[:4]}-{paths[0].name[4:6]}-{paths[0].name[6:8]}')
    with Dataset(paths[-1]) as data:
        t = data['time']
        last = num2date(t[:], t.units, only_use_cftime_datetimes=False)[-1].date()-timedelta(days=1)
    report = {'status': 'running', 'start': str(first), 'end': str(last),
              'job_id': os.environ.get('SLURM_JOB_ID'),
              'scope': 'scan accepted archive; only changed/missing summaries rewritten'}
    state = root / 'forcing/status/nrt-summaries/latest.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(state, report)
    completed = subprocess.run([sys.executable, str(root / 'bin/backfill_forcing_summaries.py'),
        '--stream', 'nrt', '--input-root', str(output / 'hourly'), '--output-root', str(output),
        '--start', str(first), '--end', str(last), '--complete-months-only', '--workers', '8'], check=False)
    report.update(status='passed' if completed.returncode == 0 else 'failed', returncode=completed.returncode)
    _atomic_json(state, report)
    raise SystemExit(completed.returncode)


if __name__ == '__main__':
    main()
