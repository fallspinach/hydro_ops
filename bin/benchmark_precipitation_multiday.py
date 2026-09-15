"""Isolated daily/multi-day precipitation benchmark; no production publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.operations import (
    OperationalLayout,
    discover_precipitation_candidates,
    discover_stage4_six_hour,
)
from hydro_ops.forcing.precipitation_day import process_precipitation_day


def hours(start, days):
    return [start-timedelta(hours=5)+timedelta(hours=i) for i in range(24*days+6)]


def fingerprint(path):
    with Dataset(path) as data:
        data.set_auto_maskandscale(False)
        return {name: hashlib.sha256(np.ascontiguousarray(var[:]).tobytes()).hexdigest()
                for name, var in data.variables.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2026-04-11')
    parser.add_argument('--days', type=int, default=3)
    args = parser.parse_args()
    if not 2 <= args.days <= 3:
        raise ValueError('Scratch-bounded pilot permits two or three days')
    root = Path(__file__).resolve().parents[1]
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    layout = OperationalLayout.project_defaults(root)
    job = os.environ['SLURM_JOB_ID']
    results = root/'forcing/work/precipitation-multiday-benchmark'/f'job_{job}'
    results.mkdir(parents=True, exist_ok=False)
    scratch = Path('/scratch')/os.environ['SLURM_JOB_USER']/f'job_{job}'
    scratch.mkdir(parents=True, exist_ok=True)
    all_hours = hours(start, args.days)
    inventory = {v: discover_precipitation_candidates(v, layout) for v in all_hours}
    constraints = {v: p for v in all_hours if (p := discover_stage4_six_hour(v, layout)) is not None}
    sources = {p for candidates, quality in inventory.values() for p in [*candidates.values(), quality] if p is not None}
    sources.update(constraints.values())
    def identities():
        return {str(p): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in sources}
    before = identities()
    reference = {}
    report = {'status': 'running', 'start': args.start, 'days': args.days,
              'rounds': [], 'scope': 'precipitation only; no production outputs changed'}
    try:
        for mode, workers in [('daily', 1), ('daily', 2), ('multiday', 2)]:
            row = {'mode': mode, 'workers': workers, 'batches': []}
            signatures = {}
            batches = [hours(start, args.days)] if mode == 'multiday' else [hours(start+timedelta(days=i), 1) for i in range(args.days)]
            for batch in batches:
                candidates = [inventory[v][0] for v in batch]
                quality = [inventory[v][1] for v in batch]
                products = set().union(*(set(c) for c in candidates))
                weights = {p: layout.mrms_conservative if p.startswith('mrms_')
                           else layout.stage4_conservative if p.startswith('stage4_')
                           else layout.nldas2_conservative if p == 'nldas2'
                           else layout.hrrr_conservative for p in products}
                calls = []
                original = subprocess.run
                def timed_run(*pos, _original=original, _calls=calls, **kw):
                    t = time.perf_counter()
                    try:
                        return _original(*pos, **kw)
                    finally:
                        _calls.append({'command': list(map(str, pos[0])), 'seconds': time.perf_counter()-t})
                with tempfile.TemporaryDirectory(prefix='precip-benchmark-', dir=scratch) as temporary:
                    temporary = Path(temporary)
                    t = time.perf_counter()
                    subprocess.run = timed_run
                    try:
                        outputs = process_precipitation_day(
                            batch, candidates, quality, weights, layout.target_grid, layout.remap_grid,
                            temporary/'output', work_directory=temporary,
                            cdo='/home/mpan/local/miniforge3/bin/cdo', remap_workers=workers,
                            quality_weights=layout.mrms_quality_bilinear if any(quality) else None,
                            stage4_six_hour_paths={v: constraints[v] for v in batch if v in constraints},
                            stage4_six_hour_weights=layout.stage4_conservative,
                            cnrfc_mask_path=layout.cnrfc_nwm_mask)
                    finally:
                        subprocess.run = original
                    elapsed = time.perf_counter()-t
                    t = time.perf_counter()
                    # Compare only each day's owned 00–23 hours, not outer halos.
                    owned_start = start if mode == 'multiday' else batch[5]
                    owned_end = start+timedelta(days=args.days) if mode == 'multiday' else owned_start+timedelta(days=1)
                    for valid, output in zip(batch, outputs, strict=True):
                        if owned_start <= valid < owned_end:
                            signatures[valid.isoformat()] = fingerprint(output)
                    row['batches'].append({'hours': len(batch), 'processing_seconds': elapsed,
                                           'verification_seconds': time.perf_counter()-t, 'remap_calls': calls})
                    print(json.dumps({'mode': mode, 'workers': workers, 'hours': len(batch), 'seconds': elapsed}), flush=True)
            if identities() != before:
                raise ValueError('Source files changed during comparison')
            if not reference:
                reference = signatures
            elif signatures != reference:
                raise ValueError(f'Hourly value/QC/time mismatch for {mode}/{workers}')
            if len(signatures) != 24*args.days:
                raise ValueError('Incomplete benchmark hour coverage')
            row.update(status='passed', processing_seconds=sum(b['processing_seconds'] for b in row['batches']),
                       remap_calls=sum(len(b['remap_calls']) for b in row['batches']))
            report['rounds'].append(row)
            (results/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        (results/'summary.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
