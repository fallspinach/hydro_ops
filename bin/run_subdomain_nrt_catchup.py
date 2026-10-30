"""Verified retro-to-NRT handoffs and monthly-checkpoint catch-up on isolated grids."""
# Model APIs deliberately use naive UTC datetimes.
import argparse
import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from prepare_cnrfc_hu18_test import approved as approved_cnrfc
from run_b120_production import approved as approved_b120
from run_cnrfc_hu18_pilot import check_parameters, digest
from run_cnrfc_production import run
from run_yampa_production import approved as approved_yampa
from test_cnrfc_hu18_model import integration

from hydro_ops.wrf_hydro.nrt_recovery import file_identity, snapshot_forcing
from hydro_ops.wrf_hydro.production import restart_names, write_json

CAMPAIGN = 'nrt_continuation_v1'


def snapshot_path(root, domain):
    return root / 'nwm/runs' / domain / 'nrt' / CAMPAIGN / 'forcing-snapshot'


def verified_gate(path, domain, plan_sha, mask_sha):
    gate = json.loads(path.read_text())
    if (gate.get('status') != 'passed' or gate.get('domain') != domain
            or gate.get('plan_sha256') != plan_sha or gate.get('mask_sha256') != mask_sha
            or not gate.get('nrt_handoff_verified') or not gate.get('spinup_warm_start_verified')
            or not gate.get('all_archived_records_verified')):
        raise ValueError('Matching domain/plan/mask handoff gate must pass')
    return gate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--domain', choices=('cnrfc_hu18_v1', 'yampa', 'b120'), required=True)
    parser.add_argument('--stage', choices=('gate', 'production'), required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    plan = json.loads(args.plan.read_text())
    if plan.get('authorized') is not True:
        raise ValueError('Missing catch-up authorization')
    spec = plan['domains'][args.domain]
    domain = {'cnrfc_hu18_v1': approved_cnrfc, 'yampa': approved_yampa,
              'b120': approved_b120}[args.domain](root)
    check_parameters(domain)
    mask = domain / 'masks' / ('cnrfc_hu18_masks.nc' if args.domain == 'cnrfc_hu18_v1' else f'{args.domain}_masks.nc')
    if digest(mask) != spec['mask_sha256']:
        raise ValueError('Planned domain mask changed')
    start, end = datetime.fromisoformat(spec['start']), datetime.fromisoformat(plan['end'])
    if start.year != end.year or not start + timedelta(days=2) <= end:
        raise ValueError('This bounded catch-up requires a single-year interval of at least two days')
    initial = root / spec['initial_restart_dir']
    if any(file_identity(initial / name) != spec['initial_restart_identities'][name]
           for name in restart_names(start)):
        raise ValueError('Accepted retro restart pair changed')
    state = args.plan.parent / args.domain
    state.mkdir(parents=True, exist_ok=True)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
    scratch.mkdir(parents=True, exist_ok=True)
    frozen = snapshot_path(root, args.domain)
    plan_sha = digest(args.plan)
    status = state / (args.stage + '-' + os.environ['SLURM_JOB_ID'] + '.json')
    report = {'status': 'running', 'domain': args.domain, 'stage': args.stage,
              'job_id': os.environ['SLURM_JOB_ID'], 'start': str(start), 'end': str(end),
              'mpi_ranks': int(os.environ['SLURM_NTASKS']), 'plan_sha256': plan_sha}
    write_json(status, report)
    try:
        if args.stage == 'gate':
            acceptance = json.loads((root / spec['forcing_report']).read_text())
            if (acceptance.get('status') != 'passed' or acceptance.get('domain') != args.domain
                    or acceptance.get('failed') != 0):
                raise ValueError('Job-specific forcing synchronization did not pass')
            retro_gate = json.loads((root / spec['retro_gate']).read_text())
            if (retro_gate.get('status') != 'passed' or retro_gate.get('domain') != args.domain
                    or not retro_gate.get('spinup_warm_start_verified')
                    or retro_gate.get('mask_sha256') != spec['mask_sha256']):
                raise ValueError('Inherited retro warm-start gate changed')
            lockroot = root / 'nwm/runs' / args.domain / 'nrt'
            lockroot.mkdir(parents=True, exist_ok=True)
            with (lockroot / 'production.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if frozen.exists():
                    raise FileExistsError('Inspect existing forcing snapshot before retrying handoff')
                snapshot_forcing(root / 'forcing/outputs' / args.domain / 'nrt/hourly', frozen, start, end)
                output = root / 'nwm/outputs' / args.domain / 'nrt/tests' / ('handoff-' + os.environ['SLURM_JOB_ID'])
                stop = start + timedelta(days=2)
                continuous = integration(root, domain, frozen, start, stop,
                    output / 'continuous', scratch / 'continuous', initial)
                resumed = integration(root, domain, frozen, start + timedelta(days=1), stop,
                    output / 'resumed', scratch / 'resumed',
                    output / 'continuous/restarts' / (start + timedelta(days=1)).strftime('%Y/%m'))
                differences = {}
                for filename in restart_names(stop):
                    fields = ('SMC', 'SH2O', 'SOIL_T', 'SNEQV') if filename.startswith('RESTART') else (
                        'hlink', 'qlink1', 'qlink2', 'z_gwsubbas')
                    with Dataset(output / 'continuous/restarts' / stop.strftime('%Y/%m') / filename) as a, \
                            Dataset(output / 'resumed/restarts' / stop.strftime('%Y/%m') / filename) as b:
                        for field in fields:
                            x, y = np.ma.asarray(a[field][:]), np.ma.asarray(b[field][:])
                            np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(y))
                            np.testing.assert_allclose(x.compressed(), y.compressed(), rtol=1e-5, atol=1e-6)
                            differences[field] = float(np.ma.max(np.ma.abs(x.astype('f8') - y.astype('f8'))))
                gate = report | {'status': 'passed', 'mask_sha256': spec['mask_sha256'],
                    'spinup_warm_start_verified': True, 'nrt_handoff_verified': True,
                    'continuous': continuous, 'resumed': resumed, 'max_abs_differences': differences,
                    'all_archived_records_verified': continuous['all_archived_records_verified']
                                                      and resumed['all_archived_records_verified'],
                    'midnight_merge_tested': True, 'archive_policy': 'explicit_record_stack_v1',
                    'initial_restart_sha256': {n: digest(initial / n) for n in restart_names(start)},
                    'operational_hook_enabled': False, 'scientific_validation': False}
                write_json(state / 'handoff-acceptance.json', gate)
        else:
            gate_path = state / 'handoff-acceptance.json'
            gate = verified_gate(gate_path, args.domain, plan_sha, spec['mask_sha256'])
            if any(digest(initial / n) != gate['initial_restart_sha256'][n] for n in restart_names(start)):
                raise ValueError('Gated initial restart pair changed')
            snapshot = json.loads((frozen / 'snapshot.json').read_text())
            for original, identity in snapshot['source_identities'].items():
                relative = Path(original).relative_to(root / 'forcing/outputs' / args.domain / 'nrt/hourly')
                if file_identity(frozen / relative) != identity:
                    raise ValueError('Pinned forcing inode changed')
            run(root, start.year, campaign=CAMPAIGN, archive_gate=gate_path, stream='nrt',
                start_date=start.strftime('%Y-%m-%d'), end_date=end.strftime('%Y-%m-%d'),
                initial_restart_dir=initial, forcing_root=frozen, domain=args.domain,
                continuation_end=end)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(status, report)


if __name__ == '__main__':
    main()
