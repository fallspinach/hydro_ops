"""Isolated three-day cold starts and one-day option-1 continuations on HU18 candidate."""
import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from prepare_cnrfc_hu18_test import CASES, DOMAIN, approved, digest, identity
from run_cnrfc_production import daily_channels

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import (
    check_active_state,
    check_forcing,
    copy_atomic,
    namelist,
    output_inventory,
    publish_hourly,
    restart_check,
    restart_names,
    validate_outputs,
    write_json,
)


def integration(root, domain, forcing, start, end, result, scratch, initial=None):
    result.mkdir(parents=True, exist_ok=False)
    scratch.mkdir(parents=True, exist_ok=False)
    _, masks, lat, lon = load_masks(domain/'parameters/domain_masks.nc')
    active = masks['model_mask']
    check_forcing(forcing, start, end)
    identities = {}
    day = start
    while day <= end:
        path = forcing/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        audit = json.loads(path.with_name(path.name+'.subset.json').read_text())
        if audit.get('status') != 'passed' or audit.get('missing_active_values') != 0 or audit.get('output_identity') != identity(path):
            raise ValueError('Invalid/stale candidate forcing receipt')
        identities[str(path)] = identity(path)
        with Dataset(path) as ds:
            np.testing.assert_allclose(ds['lat'][:], lat, atol=1e-5, rtol=0)
            np.testing.assert_allclose(ds['lon'][:], lon, atol=1e-5, rtol=0)
        check_active_state(path, active, ('T2D','Q2D','PSFC','U2D','V2D','RAINRATE','SWDOWN','LWDOWN'))
        day += timedelta(days=1)
    shutil.copytree(domain/'parameters', scratch/'DOMAIN')
    template = root/'nwm/runs/nwm_subset_mid_atlantic/run_prism_native_daily'
    for name in ('namelist.hrldas','hydro.namelist','CHANPARM.TBL','GENPARM.TBL','HYDRO.TBL','MPTABLE.TBL','SOILPARM.TBL'):
        shutil.copy2(template/name, scratch/name)
    (scratch/'wrf_hydro.exe').symlink_to(root/'external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe')
    if initial:
        restart_check([initial/n for n in restart_names(start)], start)
    namelist(scratch/'namelist.hrldas', {
        'INDIR': repr(str(forcing)), 'OUTDIR': "'./'", 'START_YEAR': start.year,
        'START_MONTH': start.month, 'START_DAY': start.day, 'START_HOUR': 0, 'START_MIN': 0,
        'KHOUR': int((end-start).total_seconds()/3600), 'PCP_PARTITION_OPTION': 1,
        'RESTART_FILENAME_REQUESTED': repr(str(initial/restart_names(start)[0])) if initial else "' '",
        'RESTART_FREQUENCY_HOURS': 24, 'OUTPUT_TIMESTEP': 3600, 'SPLIT_OUTPUT_COUNT': 1})
    namelist(scratch/'hydro.namelist', {
        'RESTART_FILE': repr(str(initial/restart_names(start)[1])) if initial else "' '",
        'rst_dt': 1440, 'rst_typ': 1 if initial else 0,
        'RSTRT_SWC': 0 if initial else 1, 'GW_RESTART': 1 if initial else 0,
        't0OutputFlag': 0, 'CHRTOUT_DOMAIN': 1, 'CHRTOUT_HOURLY': 1, 'CHRTOUT_DAILY': 1,
        'LDASOUT_HOURLY': 0, 'LDASOUT_DAILY': 1, 'RTOUT_DOMAIN': 0, 'LSMOUT_DOMAIN': 0,
        'CHANOBS_DOMAIN': 0, 'CHRTOUT_GRID': 0, 'output_gw': 0, 'outlake': 0, 'frxst_pts_out': 0,
        'DTRT_TER': 600, 'DTRT_CH': 600, 'lake_option': 0, 'diversions_file': "''",
        'io_form_outputs': 3, 'out_dt': 60, 'SPLIT_OUTPUT_COUNT': 1})
    for name in ('namelist.hrldas', 'hydro.namelist'):
        shutil.copy2(scratch/name, result/name)
    started = time.monotonic()
    with (result/'model.log').open('w') as log:
        subprocess.run(['mpiexec','-n',os.environ['SLURM_NTASKS'],'./wrf_hydro.exe'],
                       cwd=scratch, stdout=log, stderr=subprocess.STDOUT, check=True)
    model_seconds = time.monotonic()-started
    if 'The model finished successfully' not in (result/'model.log').read_text():
        raise ValueError('Model success sentinel missing')
    if any(identity(Path(p)) != v for p,v in identities.items()):
        raise ValueError('Test forcing changed during model run')
    hourly, daily = validate_outputs(scratch, start, end)
    channels = daily_channels(scratch, start, end, hourly)
    for path in daily:
        check_active_state(path, active, ('SOIL_M','SOIL_T','SNEQV'))
    day = start+timedelta(days=1)
    while day <= end:
        pair = [scratch/n for n in restart_names(day)]
        restart_check(pair, day)
        check_active_state(pair[0], active, ('SMC','SH2O','SOIL_T','SNEQV'))
        for path in pair:
            copy_atomic(path, result/'restarts'/day.strftime('%Y/%m')/path.name)
        day += timedelta(days=1)
    publish_hourly(hourly[:24], result/'hourly', None)
    if hourly[24:]:
        publish_hourly(hourly[24:], result/'hourly', None)
    for path in [*daily, *channels]:
        copy_atomic(path, result/'daily'/path.name)
    output_inventory(scratch, result)
    report = {'status': 'passed', 'start': str(start), 'end': str(end),
              'rst_typ': 1 if initial else 0, 'cold_start': initial is None,
              'hourly_records': len(hourly), 'daily_land_records': len(daily),
              'daily_channel_records': len(channels), 'model_seconds': model_seconds,
              'forcing_identities': identities, 'all_archived_records_verified': True}
    write_json(result/'acceptance.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation-report', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    domain = approved(root)
    preparation = json.loads(args.preparation_report.read_text())
    if preparation.get('status') != 'passed' or preparation.get('domain') != DOMAIN:
        raise ValueError('Candidate preparation gate failed')
    acceptance = json.loads((domain/'parameter_acceptance.json').read_text())
    if acceptance['status'] != 'passed':
        raise ValueError('Parameter gate failed')
    for name, info in acceptance['files'].items():
        if digest(domain/'parameters'/name) != info['sha256']:
            raise ValueError(f'Parameter changed: {name}')
    job = os.environ['SLURM_JOB_ID']
    output = root/'nwm/outputs'/DOMAIN/'tests'/f'job-{job}'
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/hu18-model')
    report = {'status':'running', 'domain':DOMAIN, 'job_id':job, 'mpi_ranks':int(os.environ['SLURM_NTASKS']),
              'scientific_validation':False, 'operational_cutover':False, 'cases':[]}
    try:
        for stream, first in CASES:
            start = datetime.fromisoformat(first)
            end, resume = start+timedelta(days=3), start+timedelta(days=2)
            forcing = root/'forcing/outputs'/DOMAIN/'tests'/stream/'hourly'
            case = output/stream
            continuous = integration(root, domain, forcing, start, end,
                case/'continuous', scratch/stream/'continuous')
            initial = case/'continuous/restarts'/resume.strftime('%Y/%m')
            continued = integration(root, domain, forcing, resume, end,
                case/'resumed', scratch/stream/'resumed', initial=initial)
            maxima = {}
            for filename, variables in zip(restart_names(end),
                    [('SMC','SH2O','SOIL_T','SNEQV'), ('hlink','qlink1','qlink2','z_gwsubbas')]):
                with Dataset(case/'continuous/restarts'/end.strftime('%Y/%m')/filename) as a, Dataset(case/'resumed/restarts'/end.strftime('%Y/%m')/filename) as b:
                    for name in variables:
                        x,y = np.ma.asarray(a[name][:]),np.ma.asarray(b[name][:])
                        np.testing.assert_array_equal(np.ma.getmaskarray(x),np.ma.getmaskarray(y))
                        np.testing.assert_allclose(x.compressed(),y.compressed(),rtol=1e-5,atol=1e-6)
                        maxima[name] = float(np.ma.max(np.ma.abs(x.astype('f8')-y.astype('f8'))))
            report['cases'].append({'stream':stream, 'continuous':continuous, 'resumed':continued,
                                    'restart_equivalence':'passed', 'max_abs_differences':maxima})
            write_json(output/'acceptance.json',report)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        write_json(output/'acceptance.json',report)


if __name__ == '__main__':
    main()
