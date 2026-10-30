"""CNRFC annual production with monthly checkpoints and dual channel outputs."""
# Model scheduling APIs use naive UTC datetimes.
# ruff: noqa: DTZ001, DTZ007
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date
from subset_nwm_restart import subset_pair

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import (
    check_active_state,
    check_forcing,
    copy_atomic,
    dates,
    months,
    namelist,
    output_inventory,
    publish_hourly,
    restart_check,
    restart_names,
    validate_outputs,
    write_json,
)


def daily_channels(work, start, stop, hourly):
    paths = sorted(work.glob('*.CHRTOUT_DOMAIN1.daily'))
    days = list(dates(start, stop))
    if [p.name[:8] for p in paths] != [d.strftime('%Y%m%d') for d in days]:
        raise ValueError('Daily channel coverage mismatch')
    for path, day in zip(paths, days):
        with Dataset(path) as data:
            bounds = num2date(data['time_bounds'][:].reshape(-1), data['time'].units)
            if list(map(str, bounds)) != [str(day), str(day + timedelta(days=1))]:
                raise ValueError('Daily channel time bounds mismatch')
            for name, var in data.variables.items():
                if 'time' not in var.dimensions or name in ('time', 'time_bounds'):
                    continue
                if 'time:' not in getattr(var, 'cell_methods', ''):
                    raise ValueError(f'Missing reduction metadata: {name}')
                values = np.ma.asarray(var[:])
                if not np.isfinite(values.compressed()).all():
                    raise ValueError(f'Nonfinite daily channel values: {name}')
            flow = np.ma.asarray(data['streamflow'][:])
            if not flow.count():
                raise ValueError('Empty daily streamflow')
    # Numerical oracle on the first complete day of every segment.
    samples = []
    for path in hourly[:24]:
        with Dataset(path) as data:
            samples.append(np.ma.asarray(data['streamflow'][:]).reshape(-1))
    expected = np.ma.mean(np.ma.stack(samples), axis=0)
    with Dataset(paths[0]) as data:
        actual = np.ma.asarray(data['streamflow'][:]).reshape(-1)
    np.testing.assert_array_equal(np.ma.getmaskarray(actual), np.ma.getmaskarray(expected))
    np.testing.assert_allclose(actual.compressed(), expected.compressed(), rtol=2e-5, atol=1e-6)
    return paths


def simulation_window(year, end_date=None, start_date=None):
    first = (datetime(year, 1, 2 if year == 1979 else 1) if start_date is None
             else datetime.strptime(start_date, '%Y-%m-%d'))
    end = datetime(year + 1, 1, 1) if end_date is None else datetime.strptime(end_date, '%Y-%m-%d')
    if year < 1979 or first.year != year or not first < end <= datetime(year + 1, 1, 1):
        raise ValueError('End must be after the start and no later than next January 1')
    return first, end


def publication_roots(root, campaign, stream='retro', domain='cnrfc'):
    if domain != 'cnrfc':
        if (domain in ('cnrfc_hu18_v1', 'yampa', 'b120') and stream == 'nrt'
                and campaign == 'nrt_continuation_v1'):
            promotion = root / 'nwm/status' / domain / 'nrt-promotion/promotion.json'
            if promotion.exists():
                if json.loads(promotion.read_text()).get('status') != 'complete':
                    raise RuntimeError('NRT promotion is incomplete; inspect its journal')
                return (root / 'nwm/outputs' / domain / stream,
                        root / 'nwm/restarts' / domain / stream)
            return (root / 'nwm/outputs' / domain / stream / campaign,
                    root / 'nwm/restarts' / domain / stream / campaign)
        campaigns = {'cnrfc_hu18_v1': 'hu18_pilot_v1', 'b120': 'production_v1',
                     'ucrb': 'production_v1', 'yampa': 'production_v1'}
        if domain not in campaigns or stream != 'retro' or campaign != campaigns[domain]:
            raise ValueError('Unsupported isolated domain campaign')
        promotion = root / 'nwm/status' / domain / 'retro-promotion/promotion.json'
        if promotion.exists():
            if json.loads(promotion.read_text()).get('status') != 'complete':
                raise RuntimeError('Retro promotion is incomplete; inspect its journal')
            return (root / 'nwm/outputs' / domain / stream,
                    root / 'nwm/restarts' / domain / stream)
        return (root / 'nwm/outputs' / domain / stream / campaign,
                root / 'nwm/restarts' / domain / stream / campaign)
    if stream == 'nrt':
        if campaign not in ('nrt_warmstart_test_v1', 'nrt_continuation_v1'):
            raise ValueError('NRT requires an NRT campaign')
        outputs = root / 'nwm/outputs/cnrfc/nrt'
        restarts = root / 'nwm/restarts/cnrfc/nrt'
        if campaign == 'nrt_warmstart_test_v1':
            return outputs / 'tests' / campaign, restarts / 'tests' / campaign
        return outputs, restarts
    if stream != 'retro' or campaign not in ('production_1979_1981_v1', 'recovery_record_stack_v1'):
        raise ValueError('Invalid stream/campaign combination')
    from hydro_ops.cnrfc_domain import require_legacy_grid
    require_legacy_grid(root)
    outputs = root / 'nwm/outputs/cnrfc/retro' / campaign
    restarts = root / 'nwm/restarts/cnrfc/retro' / campaign
    promotion = root / 'nwm/status/cnrfc/retro-promotion/promotion.json'
    if campaign == 'recovery_record_stack_v1' and promotion.exists():
        if json.loads(promotion.read_text()).get('status') != 'complete':
            raise RuntimeError('CNRFC promotion is incomplete; inspect its journal')
        return outputs.parent, restarts.parent
    return outputs, restarts


def forcing_identity(path):
    info = path.stat()
    return [info.st_ino, info.st_size, info.st_mtime_ns]


def run(root, year, campaign='production_1979_1981_v1', archive_gate=None, end_date=None,
        stream='retro', start_date=None, initial_restart_dir=None, *,
        cycle_workspace=None, stream_lock=None, forcing_root=None, domain='cnrfc', continuation_end=None):
    isolated_nrt = domain in ('cnrfc_hu18_v1', 'yampa', 'b120') and stream == 'nrt'
    if forcing_root is not None and (stream != 'nrt' or (cycle_workspace is None and not isolated_nrt)):
        raise ValueError('Explicit forcing snapshots require staged NRT execution')
    first, end = simulation_window(year, end_date, start_date)
    outputs, restarts = publication_roots(root, campaign, stream, domain)
    if domain != 'cnrfc':
        authorized = year in (1979, 1980) or (
            continuation_end is not None and year >= 1981 and end <= continuation_end)
        if not authorized or archive_gate is None:
            raise ValueError('HU18 pilot requires its warm-start gate and an authorized year')
        gate = json.loads(archive_gate.read_text())
        if (gate.get('status') != 'passed' or gate.get('domain') != domain
                or not gate.get('spinup_warm_start_verified')):
            raise ValueError('HU18 spin-up warm-start gate has not passed')
        if isolated_nrt and (not gate.get('nrt_handoff_verified') or forcing_root is None):
            raise ValueError('Isolated NRT requires a verified handoff and pinned forcing snapshot')
    if stream == 'nrt' and (start_date is None or end_date is None or initial_restart_dir is None):
        raise ValueError('NRT requires explicit start/end dates and initial restart directory')
    if stream == 'retro' and (start_date is not None or initial_restart_dir is not None):
        raise ValueError('Explicit continuation inputs are currently NRT-only')
    if campaign == 'recovery_record_stack_v1' or stream == 'nrt':
        if archive_gate is None:
            raise ValueError('Recovery production requires the archive acceptance report')
        gate = json.loads(archive_gate.read_text())
        if (gate.get('status') != 'passed' or not gate.get('all_archived_records_verified')
                or not gate.get('midnight_merge_tested')
                or gate.get('archive_policy') != 'explicit_record_stack_v1'):
            raise ValueError('Archive recovery acceptance gate has not passed')
    runs = root / 'nwm/runs' / domain / stream / campaign
    if cycle_workspace is not None:
        if stream != 'nrt' or stream_lock is None:
            raise ValueError('Cycle staging requires NRT and the held stream lock')
        runs = cycle_workspace / 'runs' / str(year)
        outputs, restarts = cycle_workspace / 'outputs', cycle_workspace / 'restarts'
    runs.mkdir(parents=True, exist_ok=True)
    lock_path = (runs.parent if stream == 'nrt' else runs) / 'production.lock'
    with (nullcontext(stream_lock) if stream_lock is not None else lock_path.open('a')) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
        scratch.mkdir(parents=True, exist_ok=True)
        parameters = root / 'nwm/static/domains' / domain / 'parameters'
        _, masks, _, _ = load_masks(parameters / 'domain_masks.nc')
        active = masks['model_mask']
        if initial_restart_dir is not None:
            inputs = [initial_restart_dir.resolve() / name for name in restart_names(first)]
        elif year == 1979:
            initial = runs / 'initialization'
            if not (initial / 'subset_restart.json').exists():
                parent = root / 'nwm/runs/conus/retro/production_1979_v1/initialization-preserved-counter'
                subset_pair(parent / restart_names(first)[0], parent / restart_names(first)[1],
                            root / 'nwm/static/operational/nwm.v3.1.6/domain/RouteLink_CONUS.nc',
                            parameters, scratch / 'initialization')
                shutil.copytree(scratch / 'initialization', initial)
                shutil.copy2(parent / 'initialization.json', initial / 'parent_initialization.json')
            inputs = [initial / name for name in restart_names(first)]
        else:
            inputs = [restarts / first.strftime('%Y/%m') / name for name in restart_names(first)]
        restart_check(inputs, first)
        forcing = (Path(forcing_root) if forcing_root is not None
                   else root / 'forcing/outputs' / domain / stream / 'hourly')
        for begin, stop in months(first, end):
            segment = (begin.strftime('%Y%m%d') + '-' + stop.strftime('%Y%m%d')
                       if stream == 'nrt' else begin.strftime('%Y%m'))
            logs = runs / segment
            marker = logs / 'accepted.json'
            if marker.exists():
                record = json.loads(marker.read_text())
                if (not record.get('passed') or record.get('start') != str(begin)
                        or record.get('end') != str(stop)):
                    raise ValueError('Invalid monthly acceptance marker or interval mismatch')
                inputs = [Path(p) for p in record['restarts']]
                restart_check(inputs, stop)
                continue
            started = time.monotonic()
            logs.mkdir(parents=True, exist_ok=True)
            report = {'status': 'running', 'start': begin, 'end': stop,
                      'stream': stream, 'forcing': forcing, 'initial_restarts': inputs,
                      'outputs': outputs, 'restart_root': restarts,
                      'job_id': os.environ['SLURM_JOB_ID'], 'mpi_ranks': int(os.environ['SLURM_NTASKS'])}
            write_json(logs / 'status.json', report)
            work = scratch / ('cnrfc-' + segment)
            try:
                # The concurrent backfill may not yet have reached this month.
                deadline = time.monotonic() + 7200
                while True:
                    required = [forcing / d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                                for d in dates(begin, stop + timedelta(days=1))]
                    if all(p.exists() and p.with_name(p.name + '.subset.json').exists() for p in required):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Forcing backfill not ready after two hours; retry from checkpoint')
                    time.sleep(30)
                identities = {path: forcing_identity(path) for path in required}
                check_forcing(forcing, begin, stop)
                for path in required:
                    audit = json.loads(path.with_name(path.name + '.subset.json').read_text())
                    if audit.get('status') != 'passed' or audit.get('missing_active_values') != 0:
                        raise ValueError(f'Forcing audit failed: {path}')
                    if (stream == 'nrt' or domain != 'cnrfc') and audit.get('output_identity') != identities[path]:
                        raise ValueError(f'Stale forcing audit: {path}')
                report['forcing_identities'] = {str(p): value for p, value in identities.items()}
                write_json(logs / 'status.json', report)
                restart_check(inputs, begin)
                check_active_state(inputs[0], active, ('SMC', 'SH2O', 'SOIL_T', 'SNEQV'))
                work.mkdir(exist_ok=False)
                shutil.copytree(parameters, work / 'DOMAIN')
                template = root / 'nwm/runs/nwm_subset_mid_atlantic/run_prism_native_daily'
                for name in ('namelist.hrldas', 'hydro.namelist', 'CHANPARM.TBL', 'GENPARM.TBL',
                             'HYDRO.TBL', 'MPTABLE.TBL', 'SOILPARM.TBL'):
                    shutil.copy2(template / name, work)
                executable = root / 'external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe'
                (work / 'wrf_hydro.exe').symlink_to(executable)
                hours = int((stop - begin).total_seconds() / 3600)
                namelist(work / 'namelist.hrldas', {
                    'INDIR': repr(str(forcing)), 'OUTDIR': "'./'", 'START_YEAR': begin.year,
                    'START_MONTH': begin.month, 'START_DAY': begin.day, 'START_HOUR': 0, 'START_MIN': 0,
                    'KHOUR': hours, 'PCP_PARTITION_OPTION': 1,
                    'RESTART_FILENAME_REQUESTED': repr(str(inputs[0])),
                    'RESTART_FREQUENCY_HOURS': 24 if cycle_workspace else hours,
                    'OUTPUT_TIMESTEP': 3600, 'SPLIT_OUTPUT_COUNT': 1,
                })
                namelist(work / 'hydro.namelist', {
                    'RESTART_FILE': repr(str(inputs[1])), 'rst_dt': 1440 if cycle_workspace else hours * 60,
                    'rst_typ': 1, 'RSTRT_SWC': 0, 'GW_RESTART': 1, 't0OutputFlag': 0,
                    'CHRTOUT_DOMAIN': 1, 'CHRTOUT_HOURLY': 1, 'CHRTOUT_DAILY': 1,
                    'LDASOUT_HOURLY': 0, 'LDASOUT_DAILY': 1, 'RTOUT_DOMAIN': 0,
                    'LSMOUT_DOMAIN': 0, 'CHANOBS_DOMAIN': 0, 'CHRTOUT_GRID': 0,
                    'output_gw': 0, 'outlake': 0, 'frxst_pts_out': 0,
                    'DTRT_TER': 600, 'DTRT_CH': 600, 'lake_option': 0, 'diversions_file': "''",
                    'io_form_outputs': 3, 'out_dt': 60, 'SPLIT_OUTPUT_COUNT': 1,
                })
                for name in ('namelist.hrldas', 'hydro.namelist'):
                    shutil.copy2(work / name, logs)
                report['rst_typ'] = int(re.search(r'^\s*rst_typ\s*=\s*(\d+)',
                                                 (work / 'hydro.namelist').read_text(), re.MULTILINE | re.IGNORECASE)[1])
                model_started = time.monotonic()
                with (logs / 'model.log').open('w') as log:
                    subprocess.run(['mpiexec', '-n', str(report['mpi_ranks']), './wrf_hydro.exe'],
                                   cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
                report['model_seconds'] = time.monotonic() - model_started
                if 'The model finished successfully' not in (logs / 'model.log').read_text():
                    raise ValueError('Model completion sentinel missing')
                if (stream == 'nrt' or domain != 'cnrfc') and any(forcing_identity(p) != value for p, value in identities.items()):
                    raise ValueError('NRT forcing changed during simulation; retry with consistent inputs')
                hourly, daily = validate_outputs(work, begin, stop)
                channels = daily_channels(work, begin, stop, hourly)
                terminal = [work / name for name in restart_names(stop)]
                restart_check(terminal, stop)
                check_active_state(terminal[0], active, ('SMC', 'SH2O', 'SOIL_T', 'SNEQV'))
                for path in daily:
                    check_active_state(path, active, ('SOIL_M', 'SOIL_T', 'SNEQV'))
                checkpoints = list(dates(begin + timedelta(days=1), stop + timedelta(days=1))) if cycle_workspace else [stop]
                for checkpoint in checkpoints:
                    pair = [work / name for name in restart_names(checkpoint)]
                    restart_check(pair, checkpoint)
                    check_active_state(pair[0], active, ('SMC', 'SH2O', 'SOIL_T', 'SNEQV'))
                publish_hourly(hourly, outputs / 'hourly', Path(os.sys.executable).parent / 'ncrcat')
                for path in [*daily, *channels]:
                    copy_atomic(path, outputs / 'daily' / path.name[:4] / path.name[4:6] / path.name)
                inputs = [restarts / stop.strftime('%Y/%m') / name for name in restart_names(stop)]
                for checkpoint in checkpoints:
                    for name in restart_names(checkpoint):
                        copy_atomic(work / name, restarts / checkpoint.strftime('%Y/%m') / name)
                restart_check(inputs, stop)
                report.update(status='passed', passed=True, restarts=inputs,
                              hourly_channel_records=len(hourly), daily_land_files=len(daily),
                              daily_channel_files=len(channels), daily_streamflow_oracle='first day passed',
                              checkpoint_times=list(map(str, checkpoints)),
                              elapsed_seconds=time.monotonic() - started)
                write_json(marker, report)
                # Only successfully published job-owned monthly scratch is removed.
                shutil.rmtree(work)
                print(json.dumps(report, default=str), flush=True)
            except BaseException as error:
                report.update(status='failed', error=str(error))
                if work.exists():
                    output_inventory(work, logs)
                    # Preserve originals on failure; node scratch may be purged at job end.
                    recovery = outputs / 'failed_raw' / os.environ['SLURM_JOB_ID'] / begin.strftime('%Y%m')
                    for path in work.glob('*.CHRTOUT_DOMAIN1'):
                        copy_atomic(path, recovery / path.name)
                raise
            finally:
                report['elapsed_seconds'] = time.monotonic() - started
                write_json(logs / 'status.json', report)
        completion = (f'{first:%Y%m%d}-{end:%Y%m%d}-passed.json' if stream == 'nrt'
                      else f'{year}-passed.json')
        write_json(runs / completion, {'passed': True, 'start': first, 'end': end,
                                      'stream': stream, 'restarts': inputs})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--stream', choices=('retro', 'nrt'), default='retro')
    parser.add_argument('--start-date', help='NRT continuation start at 00 UTC (YYYY-MM-DD)')
    parser.add_argument('--initial-restart-dir', type=Path, help='Read-only directory containing the initial restart pair')
    parser.add_argument('--end-date', help='Exclusive simulation end at 00 UTC (YYYY-MM-DD); forcing at this instant is required')
    parser.add_argument('--campaign', choices=('production_1979_1981_v1', 'recovery_record_stack_v1',
                                               'nrt_warmstart_test_v1', 'nrt_continuation_v1'),
                        default='production_1979_1981_v1')
    parser.add_argument('--archive-gate', type=Path)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.year, args.campaign, args.archive_gate, args.end_date,
        args.stream, args.start_date, args.initial_restart_dir)
