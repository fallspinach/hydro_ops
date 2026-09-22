#!/usr/bin/env bash
#SBATCH --job-name=forcing-conus-daily-summary-19810130-benchmark
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=04:00:00
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
export PYTHONPATH="$root/src"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
result="$root/forcing/work/daily-summary-benchmark/job_${SLURM_JOB_ID:?}"
mkdir -p "$result"
# Read and publish on permanent storage to measure the current operational path.
# Eight CPUs reserve memory; the reducer itself is single-process/single-thread.
/usr/bin/time -v -o "$result/timing.txt" \
    "$python" "$root/bin/aggregate_forcing.py" \
    --input-root "$root/forcing/outputs/conus/retro" \
    --output-root "$result/daily" --frequency daily \
    --start 1981-01-30 --end 1981-01-30 --domain conus --stream retro \
    > "$result/aggregation.jsonl" 2> "$result/aggregation.stderr"
# Independent sampled numerical verification, excluded from aggregation timing.
"$python" - "$root" "$result" <<'PY'
import json
import sys
from pathlib import Path
import numpy as np
from netCDF4 import Dataset

root, result = map(Path, sys.argv[1:])
source = root / 'forcing/outputs/conus/retro/1981/01'
output = result / 'daily/1981/01/19810130.LDASIN_DOMAIN1.daily'
with Dataset(source / '19810130.LDASIN_DOMAIN1') as a, Dataset(source / '19810131.LDASIN_DOMAIN1') as b, Dataset(output) as o:
    ny, nx = len(o.dimensions['y']), len(o.dimensions['x'])
    assert len(o.dimensions['time']) == 1 and o.aggregation_sample_count == 24
    assert o.interval_start_utc == '1981-01-30T00:00:00Z'
    assert o.interval_end_utc == '1981-01-31T00:00:00Z'
    assert 'RAIN_DEPTH' not in o.variables and 'U2D' not in o.variables
    fields = ['T2D', 'T2D_MIN', 'T2D_MAX', 'RAINRATE', 'WIND_SPEED', 'Q2D', 'PSFC', 'SWDOWN', 'LWDOWN']
    valid_checks = {k: 0 for k in fields}
    # Distributed small tiles, including coast/ocean and interior cells.
    for y in np.linspace(0, ny-8, 5, dtype=int):
        for x in np.linspace(0, nx-8, 5, dtype=int):
            def field(k):
                return np.ma.concatenate([a[k][1:, y:y+8, x:x+8].astype('f8'), b[k][:1, y:y+8, x:x+8].astype('f8')])
            for k in fields:
                values = (np.hypot(field('U2D'), field('V2D')) if k == 'WIND_SPEED'
                          else field('T2D' if k in ('T2D_MIN', 'T2D_MAX') else k))
                mask = np.any(np.ma.getmaskarray(values) | ~np.isfinite(values.filled(np.nan)), axis=0)
                expected = (values.min(axis=0) if k == 'T2D_MIN' else
                            values.max(axis=0) if k == 'T2D_MAX' else values.mean(axis=0))
                actual = o[k][0, y:y+8, x:x+8]
                np.testing.assert_array_equal(np.ma.getmaskarray(actual), mask)
                np.testing.assert_allclose(actual.data[~mask], expected.data[~mask].astype('f4'), rtol=1e-6)
                valid_checks[k] += int((~mask).sum())
    assert all(valid_checks.values()), valid_checks
    report = {'status': 'passed', 'validation_scope': '25 distributed 8x8 tiles; not full-grid numerical audit',
              'shape': [ny, nx], 'valid_cells_checked_per_variable': valid_checks,
              'output': str(output), 'output_bytes': output.stat().st_size,
              'allocated_cpus': 8, 'reducer_processes': 1,
              'io_mode': 'permanent storage input and output', 'timing_file': str(result / 'timing.txt')}
(result / 'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
PY
cat "$result/timing.txt"
