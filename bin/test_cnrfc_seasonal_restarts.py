"""Isolated monthly-restart sensitivity experiments; never publish canonical data."""
import argparse
import fcntl
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import run_cnrfc_production as runner
from netCDF4 import Dataset
from test_cnrfc_restart_restoration import compare

from hydro_ops.wrf_hydro.production import restart_names, write_json


def flow_metrics(a, b):
    """Per-reach statistics, not a sum over the connected river network."""
    x, y = np.ma.asarray(a, dtype='f8'), np.ma.asarray(b, dtype='f8')
    np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(y))
    delta = y - x
    return {'max_abs_m3_s': float(np.ma.max(np.ma.abs(delta))),
            'mean_abs_m3_s': float(np.ma.mean(np.ma.abs(delta))),
            'mean_signed_m3_s': float(np.ma.mean(delta))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('season', choices=['spring', 'summer'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    dates = {'spring': ('2026-03-02', '2026-05-01', 'retro'),
             'summer': ('2026-07-01', '2026-09-01', 'nrt')}
    first, last, stream = dates[args.season]
    start, end = [datetime.fromisoformat(t) for t in (first, last)]
    work = root / 'nwm/runs/cnrfc/nrt/seasonal-restart-tests' / ('job-' + os.environ['SLURM_JOB_ID'])
    work.mkdir(parents=True, exist_ok=False)
    initial = work / 'initial'
    initial.mkdir()
    source = root / 'nwm/restarts/cnrfc' / stream / start.strftime('%Y/%m')
    # Freeze the common initial pair while holding the canonical writer lock.
    with (root / 'nwm/runs/cnrfc/nrt/production.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name in restart_names(start):
            shutil.copy2(source / name, initial / name)
    report = {'status': 'running', 'season': args.season, 'start': first, 'end': last,
              'restart_cadence': 'monthly', 'initial_restart_source': str(source),
              'note': 'Common inherited legacy state; sensitivity experiment, not validation against observations'}
    original = runner.namelist
    gate = root / ('nwm/outputs/cnrfc/retro/tests/'
                   'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
    try:
        # Only private destinations are written. No canonical production lock
        # is held during the integrations; the two arms share this private lock.
        with (work / 'experiment.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for mode in (0, 1):
                def configure(path, values, mode=mode):
                    if path.name == 'hydro.namelist':
                        values = dict(values, rst_typ=mode)
                    original(path, values)
                runner.namelist = configure
                runner.run(root, start.year, 'nrt_continuation_v1', gate, last, 'nrt', first,
                           initial, cycle_workspace=work / f'mode{mode}', stream_lock=lock)
        identities = []
        for mode in (0, 1):
            found = {}
            for path in (work / f'mode{mode}/runs').glob('*/*/accepted.json'):
                accepted = json.loads(path.read_text())
                assert accepted['passed'] and accepted['rst_typ'] == mode
                for name, identity in accepted['forcing_identities'].items():
                    if runner.forcing_identity(Path(name)) != identity:
                        raise ValueError('Forcing changed during sensitivity experiment')
                    found[name] = identity
            identities.append(found)
        if not identities[0] or identities[0] != identities[1]:
            raise ValueError('Experiments did not use identical forcing')
        with Dataset(root / 'nwm/static/domains/cnrfc/parameters/domain_masks.nc') as ds:
            active = np.asarray(ds['model_mask'][:], dtype=bool)
        a, b = [work / f'mode{mode}/restarts' / end.strftime('%Y/%m') for mode in (0, 1)]
        report['endpoint_state'] = compare(a, b, active, end)
        report['daily_streamflow'] = []
        volumes = [None, None]
        previous = [None, None]
        day = start
        while day < end:
            values = []
            features = []
            for mode in (0, 1):
                path = work / f'mode{mode}/outputs/daily' / day.strftime('%Y/%m/%Y%m%d.CHRTOUT_DOMAIN1.daily')
                with Dataset(path) as ds:
                    values.append(np.ma.asarray(ds['streamflow'][:], dtype='f8'))
                    features.append(ds['feature_id'][:])
            np.testing.assert_array_equal(*features)
            row = {'date': day.strftime('%Y-%m-%d'), **flow_metrics(*values)}
            if day.day == 1 and previous[0] is not None:
                row['monthly_boundary_jump_difference'] = flow_metrics(
                    values[0] - previous[0], values[1] - previous[1])
            report['daily_streamflow'].append(row)
            for mode in (0, 1):
                volume = values[mode] * 86400
                volumes[mode] = volume if volumes[mode] is None else volumes[mode] + volume
            previous = values
            day += timedelta(days=1)
        report['per_reach_volume_difference_m3'] = {
            'max_abs': float(np.ma.max(np.ma.abs(volumes[1] - volumes[0]))),
            'mean_abs': float(np.ma.mean(np.ma.abs(volumes[1] - volumes[0]))),
            'note': 'Time-integrated daily mean streamflow; do not sum connected reaches'}
        # module_sf_noahmpdrv accumulates RUNSF/RUNSB [mm/s] times DT [s].
        # The IO driver's older [m] declaration comment is inconsistent with that.
        # Same initial accumulators: endpoint differences are interval differences.
        with Dataset(a / restart_names(end)[0]) as da, Dataset(b / restart_names(end)[0]) as db:
            report['active_runoff_depth_difference_mm'] = {}
            for name in ('SFCRUNOFF', 'UDRUNOFF'):
                delta = np.ma.asarray(db[name][0], dtype='f8') - np.ma.asarray(da[name][0], dtype='f8')
                report['active_runoff_depth_difference_mm'][name] = {
                    'max_abs': float(np.ma.max(np.ma.abs(delta[active]))),
                    'mean_signed': float(np.ma.mean(delta[active]))}
        report['status'] = 'complete'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        runner.namelist = original
        write_json(work / 'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
