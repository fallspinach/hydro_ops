"""48-hour cold-start CNRFC acceptance using production subset forcing."""
import hashlib
import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import (
    check_active_state, check_forcing, copy_atomic, namelist, output_inventory,
    publish_hourly, restart_check, restart_names, validate_outputs, write_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warm-start', action='store_true')
    parser.add_argument('--archive-recovery-test', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/cnrfc-model')
    label = 'warmstart_19790201_48h' if args.warm_start else 'coldstart_19790102_48h'
    if args.archive_recovery_test:
        label = 'archive_recovery_' + label
    results = root / f'nwm/outputs/cnrfc/retro/tests/{label}/job_{job}'
    results.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'start': '1979-01-02T00:00:00Z',
              'end': '1979-01-04T00:00:00Z', 'mpi_ranks': int(os.environ['SLURM_NTASKS']),
              'cold_start': True, 'spinup_validation': False}
    started = time.monotonic()
    if args.warm_start:
        report.update(start='1979-02-01T00:00:00Z', end='1979-02-03T00:00:00Z', cold_start=False)
    try:
        domain = root / 'nwm/static/domains/cnrfc/parameters'
        acceptance = json.loads((root / 'nwm/status/cnrfc/parameter-extraction/job_4585396/acceptance.json').read_text())
        for name, expected in acceptance['files'].items():
            with (domain / name).open('rb') as handle:
                checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
            if checksum != expected['sha256']:
                raise ValueError(f'Parameter checksum changed: {name}')
        # Stage immutable parameters so any model-created auxiliaries remain on scratch.
        shutil.copytree(domain, work / 'DOMAIN')
        window, masks, lat, lon = load_masks(domain / 'domain_masks.nc')
        with Dataset(domain / 'wrfinput_CONUS.nc') as data:
            np.testing.assert_array_equal(np.asarray(data['XLAND'][:]).squeeze() == 1, masks['model_mask'])
        start, end = datetime(1979, 1, 2), datetime(1979, 1, 4)
        if args.warm_start:
            start, end = datetime(1979, 2, 1), datetime(1979, 2, 3)
        forcing = root / 'forcing/outputs/cnrfc/retro/hourly'
        check_forcing(forcing, start, end)
        days = ('19790201', '19790202', '19790203') if args.warm_start else ('19790102', '19790103', '19790104')
        for day in days:
            path = forcing / day[:4] / day[4:6] / (day + '.LDASIN_DOMAIN1')
            audit = json.loads(path.with_name(path.name + '.subset.json').read_text())
            if audit['status'] != 'passed' or audit['missing_active_values'] != 0:
                raise ValueError(f'Forcing subset audit failed: {day}')
            with Dataset(path) as data:
                np.testing.assert_allclose(data['lat'][:], lat, rtol=0, atol=1e-5)
                np.testing.assert_allclose(data['lon'][:], lon, rtol=0, atol=1e-5)
            check_active_state(path, masks['model_mask'],
                               ('T2D', 'Q2D', 'PSFC', 'U2D', 'V2D', 'RAINRATE', 'LWDOWN', 'SWDOWN'))
        template = root / 'nwm/runs/nwm_subset_mid_atlantic/run_prism_native_daily'
        for name in ('namelist.hrldas', 'hydro.namelist', 'CHANPARM.TBL', 'GENPARM.TBL',
                     'HYDRO.TBL', 'MPTABLE.TBL', 'SOILPARM.TBL'):
            shutil.copy2(template / name, work)
        executable = root / 'external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe'
        (work / 'wrf_hydro.exe').symlink_to(executable)
        namelist(work / 'namelist.hrldas', {
            'INDIR': repr(str(forcing)), 'OUTDIR': "'./'", 'START_YEAR': 1979,
            'START_MONTH': 1, 'START_DAY': 2, 'START_HOUR': 0, 'START_MIN': 0,
            'KHOUR': 48, 'PCP_PARTITION_OPTION': 1, 'RESTART_FILENAME_REQUESTED': "' '",
            'RESTART_FREQUENCY_HOURS': 48, 'OUTPUT_TIMESTEP': 3600, 'SPLIT_OUTPUT_COUNT': 1,
        })
        namelist(work / 'hydro.namelist', {
            'rst_dt': 2880, 'rst_typ': 0, 'RSTRT_SWC': 1, 'GW_RESTART': 0,
            't0OutputFlag': 0, 'CHRTOUT_DOMAIN': 1, 'CHRTOUT_HOURLY': 1,
            'CHRTOUT_DAILY': int(args.archive_recovery_test), 'LDASOUT_HOURLY': 0, 'LDASOUT_DAILY': 1,
            'RTOUT_DOMAIN': 0, 'LSMOUT_DOMAIN': 0, 'CHANOBS_DOMAIN': 0,
            'CHRTOUT_GRID': 0, 'output_gw': 0, 'outlake': 0, 'frxst_pts_out': 0,
            'DTRT_TER': 600, 'DTRT_CH': 600, 'lake_option': 0, 'diversions_file': "''",
            'io_form_outputs': 3, 'out_dt': 60, 'SPLIT_OUTPUT_COUNT': 1,
        })
        if args.warm_start:
            from subset_nwm_restart import subset_pair
            parent = root / 'nwm/restarts/conus/retro/production_1979_v1/production/1979/02'
            land, hydro = subset_pair(
                parent / restart_names(start)[0], parent / restart_names(start)[1],
                root / 'nwm/static/operational/nwm.v3.1.6/domain/RouteLink_CONUS.nc',
                domain, work / 'initial_restart')
            restart_check([land, hydro], start)
            check_active_state(land, masks['model_mask'], ('SMC', 'SH2O', 'SOIL_T', 'SNEQV'))
            for path in (land, hydro, land.parent / 'subset_restart.json'):
                copy_atomic(path, results / 'initial_restart' / path.name)
            namelist(work / 'namelist.hrldas', {
                'START_MONTH': 2, 'START_DAY': 1, 'RESTART_FILENAME_REQUESTED': repr(str(land))})
            namelist(work / 'hydro.namelist', {
                'RESTART_FILE': repr(str(hydro)), 'RSTRT_SWC': 0, 'GW_RESTART': 1})
        for name in ('namelist.hrldas', 'hydro.namelist'):
            shutil.copy2(work / name, results)
        write_json(results / 'acceptance.json', report)
        model_started = time.monotonic()
        with (results / 'model.log').open('w') as log:
            subprocess.run(['mpiexec', '-n', str(report['mpi_ranks']), './wrf_hydro.exe'],
                           cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
        report['model_seconds'] = time.monotonic() - model_started
        if 'The model finished successfully' not in (results / 'model.log').read_text():
            raise ValueError('Model completion sentinel missing')
        hourly, daily = validate_outputs(work, start, end)
        channels = []
        if args.archive_recovery_test:
            from run_cnrfc_production import daily_channels
            channels = daily_channels(work, start, end, hourly)
            # Preserve independent originals so the publication test is repeatable.
            for path in hourly:
                copy_atomic(path, results / 'raw_hourly' / path.name)
        terminal = [work / name for name in restart_names(end)]
        restart_check(terminal, end)
        check_active_state(terminal[0], masks['model_mask'], ('SMC', 'SH2O', 'SOIL_T', 'SNEQV'))
        for path in daily:
            check_active_state(path, masks['model_mask'], ('SOIL_M', 'SOIL_T', 'SNEQV'))
        if args.archive_recovery_test:
            publish_hourly(hourly[:24], results / 'hourly', None)
            publish_hourly(hourly[24:], results / 'hourly', None)
            report.update(archive_policy='explicit_record_stack_v1',
                          all_archived_records_verified=True, midnight_merge_tested=True,
                          daily_channel_records=len(channels))
        else:
            publish_hourly(hourly, results / 'hourly', Path(os.sys.executable).parent / 'ncrcat')
        for path in [*daily, *channels]:
            copy_atomic(path, results / 'daily' / path.name)
        for path in terminal:
            copy_atomic(path, results / 'restarts' / end.strftime('%Y/%m') / path.name)
        report.update(status='passed', hourly_records=len(hourly), daily_records=len(daily),
                      grid_shape=window.shape, active_cells=int(masks['model_mask'].sum()))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        output_inventory(work, results)
        report['elapsed_seconds'] = time.monotonic() - started
        write_json(results / 'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
