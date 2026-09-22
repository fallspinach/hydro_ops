#!/usr/bin/env bash
#SBATCH --job-name=forcing-conus-summary-198101-workers4-vs8
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=06:00:00
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
export PYTHONPATH="$root/src"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
result="$root/forcing/work/summary-parallel-benchmark/job_${SLURM_JOB_ID:?}"
mkdir -p "$result"
for workers in 4 8; do
    output="$root/forcing/outputs/conus/retro"
    if [[ "$workers" == 8 ]]; then output="$result/workers8"; fi
    /usr/bin/time -v -o "$result/workers${workers}.time" \
        "$python" "$root/bin/backfill_forcing_summaries.py" \
        --input-root "$root/forcing/outputs/conus/retro" --output-root "$output" \
        --start 1981-01-01 --end 1981-01-31 --workers "$workers" \
        > "$result/workers${workers}.jsonl" 2> "$result/workers${workers}.stderr"
done
# Compare every summary field, every cell, all January days and the monthly file.
"$python" - "$root" "$result" <<'PY'
import json
import sys
from pathlib import Path
import numpy as np
from netCDF4 import Dataset

root, result = map(Path, sys.argv[1:])
production = root / 'forcing/outputs/conus/retro'
relative = [Path(f'daily/1981/01/198101{day:02d}.LDASIN_DOMAIN1.daily') for day in range(1,32)]
relative.append(Path('monthly/1981/198101.LDASIN_DOMAIN1.monthly'))
for rel in relative:
    with Dataset(production / rel) as a, Dataset(result / 'workers8' / rel) as b:
        assert set(a.variables) == set(b.variables)
        for name in a.variables:
            av, bv = a[name], b[name]
            assert av.dimensions == bv.dimensions
            if 'y' not in av.dimensions:
                np.testing.assert_array_equal(av[:], bv[:])
                continue
            axis = av.dimensions.index('y')
            for y in range(0, len(a.dimensions['y']), 120):
                sl = [slice(None)] * av.ndim
                sl[axis] = slice(y, y+120)
                x, z = av[tuple(sl)], bv[tuple(sl)]
                np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(z))
                np.testing.assert_array_equal(np.ma.filled(x, np.nan), np.ma.filled(z, np.nan))
timings = {}
for workers in (4,8):
    records = [json.loads(line) for line in (result / f'workers{workers}.jsonl').read_text().splitlines()]
    timing = records[-1]
    assert timing['status'] == 'completed'
    assert sum(r.get('status') == 'published' and 'day' in r for r in records) == 31
    timings[str(workers)] = timing
report = {'status':'passed', 'compared_files':len(relative), 'comparison':'all cells and variables',
          'timings':timings, 'daily_speedup_8_vs_4':timings['4']['daily_seconds']/timings['8']['daily_seconds'],
          'caveat':'4 workers first, 8 second; cache warmth and shared I/O can affect results',
          'backfill_workers':4, 'policy':'conservative 4-worker backfill; do not auto-escalate I/O'}
(result / 'acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
PY
