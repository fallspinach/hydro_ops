"""Isolated B120 warm-start gate and bounded annual retro production."""
# Production time APIs use naive UTC.
# ruff: noqa: DTZ001
import argparse
import fcntl
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from run_cnrfc_hu18_pilot import check_parameters, digest, warm_gate
from run_cnrfc_production import run

from hydro_ops.wrf_hydro.production import restart_names, write_json

DOMAIN = 'b120'
CAMPAIGN = 'production_v1'
END = datetime(2026, 4, 18)


def approved(root, domain_name=DOMAIN):
    domain = root / 'nwm/static/domains' / domain_name
    record = json.loads((domain / 'manifest.json').read_text())
    if (record.get('status') != 'passed' or record.get('domain') != domain_name
            or record.get('mask_sha256') != digest(domain / f'masks/{domain_name}_masks.nc')
            or record.get('boundary_sha256') != digest(domain / f'boundaries/{domain_name}_boundaries.gpkg')):
        raise ValueError(f'{domain_name.upper()} domain acceptance changed')
    check_parameters(domain)
    return domain


def validate_gate(root, domain, path, domain_name=DOMAIN):
    gate = json.loads(path.read_text())
    if (gate.get('status') != 'passed' or gate.get('domain') != domain_name
            or not gate.get('spinup_warm_start_verified')
            or gate.get('mask_sha256') != digest(domain / f'masks/{domain_name}_masks.nc')):
        raise ValueError(f'{domain_name.upper()} warm-start gate missing or changed')
    initial = root / 'nwm/runs' / domain_name / 'retro' / CAMPAIGN / 'initialization'
    for name in restart_names(datetime(1979, 1, 2)):
        if digest(initial / name) != gate['initial_restart_sha256'][name]:
            raise ValueError('Gated initial restart changed')


def main(domain_name=DOMAIN):
    if domain_name not in ('b120', 'ucrb', 'yampa'):
        raise ValueError('Unsupported domain')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('gate', *map(str, range(1979, 2027))))
    parser.add_argument('--gate', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    domain = approved(root, domain_name)
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}')
    scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(scratch).free < 20 * 1024**3:
        raise RuntimeError('Insufficient scratch')
    state = root / 'nwm/status' / domain_name / CAMPAIGN / ('job-' + job)
    state.mkdir(parents=True, exist_ok=False)
    try:
        if args.stage == 'gate':
            runs = root / 'nwm/runs' / domain_name / 'retro' / CAMPAIGN
            runs.mkdir(parents=True, exist_ok=True)
            with (runs / 'production.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                warm_gate(root, domain, scratch, state, domain_name=domain_name,
                          campaign=CAMPAIGN, mask_name=f'{domain_name}_masks.nc')
        else:
            if args.gate is None:
                parser.error('Annual production requires --gate')
            validate_gate(root, domain, args.gate, domain_name)
            year = int(args.stage)
            if year > 1979:
                previous = json.loads((root / 'nwm/runs' / domain_name / 'retro' / CAMPAIGN
                                       / f'{year - 1}-passed.json').read_text())
                if not previous.get('passed') or previous.get('end') != str(datetime(year, 1, 1)):
                    raise ValueError('Previous annual acceptance missing or wrong endpoint')
            stop = min(datetime(year + 1, 1, 1), END)
            run(root, year, campaign=CAMPAIGN, archive_gate=args.gate, domain=domain_name,
                end_date=stop.strftime('%Y-%m-%d'), continuation_end=END)
        write_json(state / 'status.json', {'status': 'passed', 'stage': args.stage,
                                          'mpi_ranks': int(os.environ['SLURM_NTASKS'])})
    except BaseException as error:
        write_json(state / 'status.json', {'status': 'failed', 'stage': args.stage, 'error': str(error)})
        raise


if __name__ == '__main__':
    main()
