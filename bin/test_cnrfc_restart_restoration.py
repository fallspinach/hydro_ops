"""Isolated rst_typ=0/1 continuation diagnosis using job 4810248 checkpoints."""
import fcntl
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import run_cnrfc_production as runner
from netCDF4 import Dataset

from hydro_ops.wrf_hydro.production import restart_names, write_json


def compare(reference, candidate, active, end=datetime(2026, 10, 3)):  # noqa: DTZ001 -- model clock is naive UTC
    result = {}
    for filename, names in zip(restart_names(end),
            [('SMC', 'SH2O', 'SOIL_T', 'SNEQV'), ('hlink', 'qlink1', 'qlink2', 'z_gwsubbas')]):
        with Dataset(reference / filename) as a, Dataset(candidate / filename) as b:
            for name in names:
                x, y = np.ma.asarray(a[name][:]), np.ma.asarray(b[name][:])
                valid = (~(np.ma.getmaskarray(x) | np.ma.getmaskarray(y))
                         & (np.abs(x.data) < 1e20) & (np.abs(y.data) < 1e20))
                difference = np.abs(x.data.astype('f8') - y.data.astype('f8'))
                bad = valid & (difference > 1e-6 + 1e-5 * np.abs(y.data))
                row = {'valid': int(valid.sum()), 'mismatches': int(bad.sum()),
                       'max_abs': float(difference[valid].max()),
                       'mask_mismatches': int((np.ma.getmaskarray(x) != np.ma.getmaskarray(y)).sum())}
                if 'south_north' in a[name].dimensions:
                    shape = [1] * x.ndim
                    shape[a[name].dimensions.index('south_north')] = active.shape[0]
                    shape[a[name].dimensions.index('west_east')] = active.shape[1]
                    on = np.broadcast_to(active.reshape(shape), x.shape)
                    bad_cells = np.any(bad & on, axis=tuple(i for i, d in enumerate(a[name].dimensions)
                                                           if d not in ('south_north', 'west_east')))
                    row.update(active_values=int((valid & on).sum()), active_mismatches=int((bad & on).sum()),
                               inactive_mismatches=int((bad & ~on).sum()), active_cells=int(bad_cells.sum()),
                               active_max_abs=float(difference[valid & on].max()))
                result[name] = row
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    original = root / 'nwm/runs/cnrfc/nrt/cycles/job-4810248'
    reference = original / 'restarts/2026/10'
    prior_resume = original / 'resume-check/restarts/2026/10'
    workspace = root / 'nwm/runs/cnrfc/nrt/restart-diagnostics' / ('job-' + os.environ['SLURM_JOB_ID'])
    workspace.mkdir(parents=True, exist_ok=False)
    with Dataset(root / 'nwm/static/domains/cnrfc/parameters/domain_masks.nc') as d:
        active = np.asarray(d['model_mask'][:], dtype=bool)
    report = {'status': 'running', 'reference_job': '4810248',
              'original_comparison': compare(reference, prior_resume, active), 'variants': {}}
    output = workspace / 'report.json'
    write_json(output, report)
    old_namelist = runner.namelist
    gate = root / ('nwm/outputs/cnrfc/retro/tests/'
                   'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
    expected = json.loads((original / 'resume-check/runs/2026/20261002-20261003/accepted.json').read_text())
    try:
        with (root / 'nwm/runs/cnrfc/nrt/production.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for mode in (0, 1):
                if any(runner.forcing_identity(Path(p)) != v for p, v in expected['forcing_identities'].items()):
                    raise ValueError('Forcing differs from original comparison; controlled test invalid')

                def configure(path, values, mode=mode):
                    if path.name == 'hydro.namelist':
                        values = dict(values, rst_typ=mode)
                    old_namelist(path, values)

                runner.namelist = configure
                trial = workspace / f'rst_typ_{mode}'
                runner.run(root, 2026, 'nrt_continuation_v1', gate, '2026-10-03',
                           'nrt', '2026-10-02', reference, cycle_workspace=trial, stream_lock=lock)
                candidate = trial / 'restarts/2026/10'
                report['variants'][str(mode)] = {
                    'against_continuous': compare(reference, candidate, active),
                    'against_original_resumed': compare(prior_resume, candidate, active),
                }
                write_json(output, report)
        report['status'] = 'complete'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        runner.namelist = old_namelist
        write_json(output, report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
