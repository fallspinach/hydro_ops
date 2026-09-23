"""Isolated 1/2/4-worker masking and CDO reference benchmark on four days.

Never publishes into the production archive. Timing includes scratch staging,
integrity checks and checksum-verified transfer to the benchmark output folder.
CDO writes only the eight forcing fields: a lower-bound reference, not a
schema-preserving production replacement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from benchmark_static_mask_chunks import chunk_mask, verify, verify_chunks
from netCDF4 import Dataset
from test_static_forcing_mask import FIELDS


def identity(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def checksum(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verify_cdo(source, mask, output):
    """Check every field value, allowing CDO's own missing-value encoding."""
    started = time.monotonic()
    with Dataset(mask) as grid:
        keep = np.asarray(grid['keep'][:], bool)
    with Dataset(source) as src, Dataset(output) as dst:
        for name in FIELDS:
            if src[name].shape != dst[name].shape:
                raise ValueError(f'CDO shape mismatch: {name}')
            for t in range(src[name].shape[0]):
                expected, actual = src[name][t], dst[name][t]
                absent = np.ma.getmaskarray(expected) | ~keep
                if not np.array_equal(absent, np.ma.getmaskarray(actual)):
                    raise ValueError(f'CDO mask mismatch: {name}/{t}')
                np.testing.assert_array_equal(np.asarray(expected)[~absent], np.asarray(actual)[~absent])
        np.testing.assert_array_equal(src['time'][:], dst['time'][:])
        if src['time'].units != dst['time'].units:
            raise ValueError('CDO time units differ')
    return time.monotonic()-started


def run_file(task):
    source, mask, scratch, destination, method, cdo = map(str, task)
    source, mask, scratch, destination = map(Path, (source, mask, scratch, destination))
    started = time.monotonic()
    before = identity(source)
    report = {'source': str(source), 'method': method}
    with tempfile.TemporaryDirectory(prefix='mask-benchmark-', dir=scratch) as temporary:
        temporary = Path(temporary)
        staged, output = temporary/'source.nc', temporary/'masked.nc'
        t = time.monotonic()
        shutil.copyfile(source, staged)
        if identity(source) != before:
            raise ValueError('Source changed during staging')
        report['stage_seconds'] = time.monotonic()-t
        if method == 'chunks':
            report.update(chunk_mask(staged, mask, output))
            report['integrity'] = verify_chunks(staged, mask, output)
        else:
            command = [cdo, '-O', '-f', 'nc4', '-z', 'zip_2', 'ifthen',
                       '-selname,keep', str(mask), '-selname,'+','.join(FIELDS),
                       str(staged), str(output)]
            t = time.monotonic()
            with destination.with_suffix('.cdo.log').open('w') as log:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
            report['write_seconds'] = time.monotonic()-t
            report['integrity'] = {'seconds': verify_cdo(staged, mask, output)}
            report['scope'] = 'eight_fields_only_excludes_auxiliary_schema_restoration'
        t = time.monotonic()
        part = destination.with_suffix('.part')
        shutil.copyfile(output, part)
        with part.open('rb') as handle:
            os.fsync(handle.fileno())
        expected = checksum(output)
        if checksum(part) != expected:
            raise ValueError('Transfer checksum mismatch')
        os.replace(part, destination)
        report.update(transfer_seconds=time.monotonic()-t, sha256=expected,
                      output_bytes=destination.stat().st_size)
    report.update(total_seconds=time.monotonic()-started, status='passed')
    destination.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--cdo', default='/home/mpan/local/miniforge3/bin/cdo')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = [root/f'forcing/outputs/conus/retro/hourly/2000/01/200001{d}.LDASIN_DOMAIN1' for d in (15, 16, 17, 18)]
    mask = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(source)
    args.result.mkdir(parents=True, exist_ok=False)
    args.scratch.mkdir(parents=True, exist_ok=True)
    summary = {'status': 'running', 'rounds': [], 'job': os.environ.get('SLURM_JOB_ID'),
               'notes': 'Same four files per round; cache/load can vary. CDO omits auxiliary fields.'}
    for method, workers in [('chunks', 1), ('chunks', 2), ('chunks', 4), ('cdo', 1)]:
        folder = args.result/f'{method}-{workers}'
        folder.mkdir()
        started = time.monotonic()
        tasks = [(p, mask, args.scratch, folder/p.name, method, args.cdo) for p in sources]
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            reports = list(pool.map(run_file, tasks))
        seconds = time.monotonic()-started
        row = {'method': method, 'workers': workers, 'wall_seconds': seconds,
               'files_per_hour': len(sources)*3600/seconds, 'files': reports}
        summary['rounds'].append(row)
        (args.result/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps({k: v for k, v in row.items() if k != 'files'}), flush=True)
    # Independent full checks, outside the timed production-like rounds.
    # All 12 chunk outputs are checked; no source-audit trust is assumed here.
    for workers in (1, 2, 4):
        for source in sources:
            seconds = verify(source, mask, args.result/f'chunks-{workers}'/source.name)
            print(json.dumps({'full_audit': str(source), 'workers': workers, 'seconds': seconds}), flush=True)
    summary['status'] = 'passed'
    (args.result/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')


if __name__ == '__main__':
    main()
