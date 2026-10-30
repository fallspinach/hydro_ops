"""Submit the isolated March 2–4, 2026 CNRFC NRT warm-start test once."""
import fcntl
import json
import subprocess
from pathlib import Path

from hydro_ops.wrf_hydro.production import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    runs = root / 'nwm/runs/cnrfc/nrt/nrt_warmstart_test_v1'
    runs.mkdir(parents=True, exist_ok=True)
    manifest = runs / 'submission.json'
    with (runs / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest.exists():
            raise RuntimeError('Submission record exists; inspect it before resubmitting')
        gate = root / ('nwm/outputs/cnrfc/retro/tests/'
                       'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
        if json.loads(gate.read_text()).get('status') != 'passed':
            raise ValueError('Archive acceptance gate failed')
        command = [
            'sbatch', '--parsable', '--time=02:00:00',
            '--job-name=wrfh-cnrfc-nrt-warmstart-20260302-0304-64mpi',
            'slurm/run_cnrfc_production.sh', '--year', '2026', '--stream', 'nrt',
            '--campaign', 'nrt_warmstart_test_v1',
            '--start-date', '2026-03-02', '--end-date', '2026-03-04',
            '--initial-restart-dir', str(root / 'nwm/restarts/cnrfc/retro/2026/03'),
            '--archive-gate', str(gate),
        ]
        report = {'status': 'submitting', 'command': command, 'mpi_ranks': 64,
                  'start': '2026-03-02 00:00:00', 'end': '2026-03-04 00:00:00'}
        write_json(manifest, report)
        try:
            result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
            report.update(status='submitted', job_id=int(result.stdout.strip().split(';')[0]))
        except BaseException as error:
            report.update(status='submission_uncertain', error=str(error))
            raise
        finally:
            write_json(manifest, report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
