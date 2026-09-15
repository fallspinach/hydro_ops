"""Submit or compare paired isolated seven-day production repair benchmarks."""
import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def compare(directory):
    checked = []
    for day in range(1, 8):
        relative = Path(f'2021/09/202109{day:02}.LDASIN_DOMAIN1')
        left, right = directory/'reference'/relative, directory/'optimized'/relative
        with Dataset(left) as a, Dataset(right) as b:
            a.set_auto_maskandscale(False)
            b.set_auto_maskandscale(False)
            if set(a.variables) != set(b.variables):
                raise ValueError('Variable sets differ')
            for attr in ['archive_granularity', 'prism_reconciliation_accepted', 'cnrfc_stage4_policy',
                         'forcing_domain_policy', 'forcing_domain_content_audit']:
                if a.getncattr(attr) != b.getncattr(attr):
                    raise ValueError(f'Policy mismatch: {attr}')
            for name, var in a.variables.items():
                other = b[name]
                if var.shape != other.shape or var.dtype != other.dtype or var.dimensions != other.dimensions:
                    raise ValueError(f'Schema mismatch: {name}')
                indices = range(var.shape[0]) if var.dimensions and var.dimensions[0] == 'time' else [Ellipsis]
                for index in indices:
                    if np.asarray(var[index]).tobytes() != np.asarray(other[index]).tobytes():
                        raise ValueError(f'Values differ: {relative}/{name}/{index}')
        checked.append(str(relative))
    (directory/'acceptance.json').write_text(json.dumps({'status': 'passed', 'days': checked}, indent=2)+'\n')
    print('PASS: all seven days and all variables exactly equal', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--chunk-archives', action='store_true')
    parser.add_argument('--parallel', action='store_true')
    args = parser.parse_args()
    if args.compare:
        compare(args.compare)
        return
    root = Path(__file__).resolve().parents[1]
    directory = root/'forcing/work'/('post2020-production-benchmark-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S'))
    directory.mkdir()
    record = {'directory': str(directory), 'start': '2021-09-01', 'end': '2021-09-07'}
    record.update(chunk_archives=args.chunk_archives, parallel=args.parallel)
    previous = None
    for mode in ('reference', 'optimized'):
        task = directory/f'{mode}.jsonl'
        task.write_text(json.dumps({'start': record['start'], 'end': record['end'],
                                   'stream': 'retro', 'revision': 'stable', 'output_root': str(directory/mode)})+'\n')
        env = dict(os.environ)
        env.update(HYDRO_OPS_PROJECT_ROOT=str(root), HYDRO_OPS_PYTHON=sys.executable,
                   HYDRO_OPS_REBUILD_TASK_FILE=str(task), SLURM_ARRAY_TASK_ID='0',
                   HYDRO_OPS_REBUILD_STATIC_ENVELOPE='1',
                   HYDRO_OPS_BENCH_MULTIDAY='1' if mode == 'optimized' and not args.chunk_archives else '0',
                   HYDRO_OPS_BENCH_FAST_MASK='1' if mode == 'optimized' and not args.chunk_archives else '0',
                   HYDRO_OPS_ARCHIVE_CHUNKS='1' if mode == 'optimized' and args.chunk_archives else '0',
                   HYDRO_OPS_PRECIPITATION_REMAP_WORKERS='1', HYDRO_OPS_ASSEMBLY_WORKERS='4',
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
        command = ['sbatch', '--parsable', '--partition=shared-128', '--nodes=1', '--ntasks=1',
                   '--cpus-per-task=64', '--tmp=240000', '--time=24:00:00', '--array=0',
                   '--job-name=nwm-repair-'+('chunk-archives-' if args.chunk_archives else 'benchmark-')+mode+'-20210901-07',
                   '--output='+str(root/f'forcing/logs/post2020-benchmark-{mode}-%j.out')]
        if previous and not args.parallel:
            command.append('--dependency=afterok:'+previous)
        command.extend(['--wrap', f'{sys.executable} {root}/slurm/rebuild_post2020_forcing.py'])
        previous = subprocess.check_output(command, env=env, text=True).strip().split(';')[0]
        record[mode] = previous
        (directory/'submission.json').write_text(json.dumps(record, indent=2)+'\n')
    command = ['sbatch', '--parsable', '--partition=shared-128', '--nodes=1', '--ntasks=1',
               '--cpus-per-task=12', '--time=04:00:00', '--dependency=afterok:'+record['reference']+':'+record['optimized'],
               '--job-name=nwm-repair-benchmark-exact-comparison-20210901-07',
               '--output='+str(root/'forcing/logs/post2020-benchmark-audit-%j.out'),
               '--wrap', f'{sys.executable} {Path(__file__).resolve()} --compare {directory}']
    record['audit'] = subprocess.check_output(command, text=True).strip().split(';')[0]
    (directory/'submission.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
