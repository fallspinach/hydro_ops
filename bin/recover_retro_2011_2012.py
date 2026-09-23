"""Recover the Oct-29 aggregation failure and gate continuation on a full block audit."""
import json
import os
import runpy
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.forcing.daily_archive import verified_daily_archive
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.retro_publication import check_scratch


def main():
    project = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
    campaign = project / f'forcing/work/retro-2011-2012-recovery-{job}'
    campaign.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'job': job, 'original_controller': '4524976',
              'scope': 'one daily baseline aggregation, three retro days, full 731-day audit',
              'hourly_cleanup': 'not performed; originals preserved'}
    journal = campaign / 'acceptance.json'
    _atomic_json(journal, report)
    env = {**os.environ, 'HYDRO_OPS_PROJECT_ROOT': str(project), 'HYDRO_OPS_PYTHON': sys.executable,
           'HYDRO_OPS_RETRO_NEW_PRODUCTION': '1', 'HYDRO_OPS_REBUILD_STATIC_ENVELOPE': '1',
           'HYDRO_OPS_MIN_SCRATCH_FREE_GB': '120', 'SLURM_ARRAY_TASK_ID': '0'}
    os.environ.update(env)

    def run(*args):
        subprocess.run([sys.executable, *map(str, args)], cwd=project, env=env, check=True)

    try:
        check_scratch(scratch)
        baseline = project / 'forcing/outputs/conus/baseline/hourly'
        day = date(2011, 10, 29)
        daily = baseline / '2011/10/20111029.LDASIN_DOMAIN1'
        if not verified_daily_archive(daily, day):
            if daily.exists():
                raise RuntimeError('Unexpected existing daily file; inspect before replacing')
            staged = scratch / 'hourly'
            staged.mkdir(parents=True, exist_ok=True)
            for hour in range(24):
                source = baseline / f'2011/10/29/20111029{hour:02d}.LDASIN_DOMAIN1'
                shutil.copy2(source, staged / source.name)
            run('bin/archive_nwm_forcing_day.py', '--day', day, '--hourly-root', staged,
                '--output-root', baseline, '--work-directory', scratch)
        if not verified_daily_archive(daily, day):
            raise RuntimeError('Reaggregated baseline did not pass verification')
        run('bin/repair_nwm_forcing_domain.py', daily, '--in-place', '--work-directory', scratch)
        report['baseline_recovery'] = 'passed'
        _atomic_json(journal, report)
        task = {'start': '2011-10-28', 'end': '2011-10-30', 'stream': 'retro', 'revision': 'stable',
                'baseline_root': str(baseline), 'output_root': str(project / 'forcing/outputs/conus/retro/hourly'),
                'writer_profile': 'validated_chunks_v1'}
        task_path = campaign / 'tasks.jsonl'
        task_path.write_text(json.dumps(task) + '\n')
        env['HYDRO_OPS_PRISM_CALENDAR_TASK_FILE'] = str(task_path)
        run('slurm/produce_prism_calendar_batch.py')
        mask = project / 'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
        with Dataset(mask) as grid:
            mask_hash = str(grid.keep_sha256)
        audit = runpy.run_path(str(project / 'bin/run_retro_forcing_block.py'))['audit']
        count = audit(project, date(2011, 1, 1), date(2012, 12, 31), mask_hash)
        if count != 731:
            raise RuntimeError(f'Unexpected block audit count: {count}')
        report.update(status='passed', audited_days=count, mask_sha256=mask_hash,
                      continuation='afterok dependency may now release 4524977')
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(journal, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
