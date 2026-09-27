"""Submit the approved 1982–2026 CNRFC continuation, saving each submission."""
import argparse
import json
import subprocess
from pathlib import Path

from hydro_ops.wrf_hydro.production import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after-job', required=True, type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = root / 'nwm/runs/cnrfc/retro/recovery_record_stack_v1'
    manifest = campaign / 'continuation-1982-20260302.json'
    gate = root / ('nwm/outputs/cnrfc/retro/tests/'
                   'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
    if manifest.exists():
        raise RuntimeError('Submission manifest exists; inspect it before any resubmission')
    report = {'status': 'submitting', 'predecessor': args.after_job,
              'end': '2026-03-02 00:00:00', 'mpi_ranks': 64, 'jobs': []}
    write_json(manifest, report)
    previous = args.after_job
    try:
        for year in range(1982, 2027):
            command = ['sbatch', '--parsable', f'--dependency=afterok:{previous}',
                       f'--job-name=wrfh-cnrfc-retro-{year}-64mpi-dailyLDAS-hourly-dailyCHRT',
                       'slurm/run_cnrfc_production.sh', '--year', str(year),
                       '--campaign', 'recovery_record_stack_v1', '--archive-gate', str(gate)]
            if year == 2026:
                command += ['--end-date', '2026-03-02']
            result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
            job = int(result.stdout.strip().split(';')[0])
            report['jobs'].append({'year': year, 'job_id': job, 'afterok': previous})
            write_json(manifest, report)
            print(json.dumps(report['jobs'][-1]), flush=True)
            previous = job
        report['status'] = 'submitted'
    except BaseException as error:
        report.update(status='submission_interrupted', error=str(error))
        raise
    finally:
        write_json(manifest, report)


if __name__ == '__main__':
    main()
