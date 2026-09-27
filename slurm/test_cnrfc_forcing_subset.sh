#!/usr/bin/env bash
#SBATCH --job-name=forcing-cnrfc-subset-pilot-retro1981-2003-nrt20260412-20260924
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/cnrfc-forcing-subset-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
scratch="/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
mkdir -p "$scratch"
"$HYDRO_OPS_PYTHON" - "$root" "$scratch" <<'PY'
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

root, scratch = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / 'bin'))
from subset_nwm_forcing import subset

def identity(path):
    stat = path.stat()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

status = root / 'forcing/status/cnrfc' / ('subset-pilot-' + os.environ['SLURM_JOB_ID'])
status.mkdir(parents=True, exist_ok=False)
output_root = root / 'forcing/outputs/cnrfc'
output_root.mkdir(parents=True, exist_ok=True)
report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'],
          'allocated_cpus': 8, 'concurrent_files': 1, 'files': []}

def save():
    path = status / 'acceptance.json'
    partial = path.with_suffix('.json.part')
    partial.write_text(json.dumps(report, indent=2) + '\n')
    partial.replace(path)

with (output_root / '.subset-pilot.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    save()
    try:
        cases = [('retro', 'hourly', '19810115'), ('retro', 'hourly', '20030115'),
                 ('nrt', 'hourly', '20260412'), ('nrt', 'hourly', '20260924'),
                 ('retro', 'daily', '19810115'), ('retro', 'monthly', '198101'),
                 ('nrt', 'daily', '20260501'), ('nrt', 'monthly', '202605')]
        for stream, frequency, stamp in cases:
            relative = Path(stream) / frequency / stamp[:4]
            if frequency != 'monthly':
                relative /= stamp[4:6]
            relative /= stamp + '.LDASIN_DOMAIN1' + ('' if frequency == 'hourly' else '.' + frequency)
            source = root / 'forcing/outputs/conus' / relative
            target = output_root / relative
            audit = target.with_name(target.name + '.subset.json')
            partial = target.with_name(target.name + '.part')
            if any(path.exists() for path in (target, audit, partial)):
                raise FileExistsError(target)
            before = identity(source)
            candidate = scratch / 'cnrfc-subset-pilot' / relative
            started = time.monotonic()
            record = subset(source, candidate, root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc')
            record['subset_and_validation_seconds'] = time.monotonic() - started
            if identity(source) != before:
                raise RuntimeError(f'CONUS source changed during subsetting: {source}')
            started_copy = time.monotonic()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, partial)
            checksum = digest(candidate)
            if digest(partial) != checksum or identity(source) != before:
                raise RuntimeError('Publication checksum or source identity changed')
            partial.replace(target)
            record.update(output=str(target), sha256=checksum, output_bytes=target.stat().st_size,
                          source_identity=before, publication_seconds=time.monotonic()-started_copy,
                          total_seconds=time.monotonic()-started)
            audit.write_text(json.dumps(record, indent=2) + '\n')
            report['files'].append(record)
            save()
            print(json.dumps(record), flush=True)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()
PY
