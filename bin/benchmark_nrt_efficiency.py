"""Isolated paired mixed-source baseline and final-audit benchmark."""
import argparse
import json
import os
import shutil
import time
from contextlib import nullcontext
from datetime import UTC, date, datetime
from pathlib import Path

from benchmark_nrt_extension import compare, receipt
from netCDF4 import Dataset

from hydro_ops.experiments.nrt_efficiency import (
    audit_final_sparse,
    same_input_content,
    shared_mixed_precipitation,
)
from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', type=date.fromisoformat, default=date(2026, 9, 20))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/nrt-efficiency-{job}'
    campaign.mkdir(exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
    as_of = datetime.now(UTC)
    report = {'status': 'running', 'day': str(args.day), 'job': job,
              'scope': 'private outputs; reference-first; same workers; cache warmth caveat',
              'baseline': {}, 'audit': {}, 'input_comparison': []}
    output = campaign / 'acceptance.json'
    try:
        paths = []
        fingerprints = []
        for label in ('reference', 'optimized'):
            engine = cycle.RecentNrt(root, scratch / label, as_of,
                baseline_root=campaign / label, output_root=campaign / (label + '-unused'))
            selections, _ = cycle.baseline_plan(args.day, engine.layout, engine.config)
            if len(list(cycle.source_runs(selections))) != 2:
                raise ValueError('Benchmark requires a mixed NLDAS/HRRR day')
            started = time.monotonic()
            with shared_mixed_precipitation() if label == 'optimized' else nullcontext():
                path, record = engine.baseline_day(args.day)
            report['baseline'][label] = {'seconds': time.monotonic() - started,
                'stages': record['baseline_stage_timings'], 'file': str(path)}
            paths.append(path)
            fingerprints.append(record['input_fingerprint'])
            _atomic_json(output, report)
        if len(set(fingerprints)) != 1:
            raise ValueError('Sources changed across trials')
        compare(*paths)
        report['baseline_comparison'] = 'passed: every variable, cell, mask and variable attribute'
        source = cycle.day_path(root / 'forcing/outputs/conus/nrt/hourly', args.day)
        original = cycle.read_json(receipt(source))
        before = cycle.identity(source)
        if original.get('status') != 'passed' or original['published_identity'] != before:
            raise ValueError('NRT reference is not accepted')
        results = []
        for label in ('reference', 'optimized'):
            path = scratch / f'audit-{label}.nc'
            shutil.copyfile(source, path)
            started = time.monotonic()
            if label == 'reference':
                engine.audit_final(path, args.day, original['prism_constrained'])
                writes = 24 * 8
            else:
                writes = audit_final_sparse(engine, path, args.day, original['prism_constrained'])
            report['audit'][label] = {'seconds': time.monotonic() - started, 'record_writes': writes}
            results.append(path)
        compare(*results)
        if cycle.identity(source) != before:
            raise ValueError('Production NRT changed during audit comparison')
        report['audit_comparison'] = 'passed'
        # Bound content-check cost using one representative file of each source.
        _, plan = cycle.baseline_plan(args.day, engine.layout, engine.config)
        for marker in ('/nldas2/', '/stage4/'):
            source = Path(next(x['path'] for x in plan['files'] if marker in x['path']))
            candidate = scratch / f'candidate-{len(report["input_comparison"])}.nc'
            shutil.copyfile(source, candidate)
            with Dataset(candidate, 'a') as ds:
                ds.history = 'benchmark conversion time differs; scientific data unchanged'
            started = time.monotonic()
            same = same_input_content(source, candidate)
            if not same:
                raise ValueError('Metadata-only candidate was not recognized')
            report['input_comparison'].append({'source': str(source), 'seconds': time.monotonic() - started,
                                                'same_scientific_content': same})
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        _atomic_json(output, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
