#!/usr/bin/env bash
#SBATCH --job-name=forcing-conus-retro-summary-1981to1985-4workers
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --array=0-4%1
#SBATCH --time=24:00:00
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
export PYTHONPATH="$root/src"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
year=$((1981 + ${SLURM_ARRAY_TASK_ID:?}))
[[ "$year" =~ ^198[1-5]$ ]]
# Explicit acceptance gate in addition to scheduler afterok dependency.
"$python" - "${HYDRO_SUMMARY_BENCHMARK_ACCEPTANCE:?}" <<'PY'
import json, sys
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
assert r['status']=='passed' and r['compared_files']==32 and r['backfill_workers']==4
PY
"$python" "$root/bin/backfill_forcing_summaries.py" \
    --input-root "$root/forcing/outputs/conus/retro/hourly" \
    --output-root "$root/forcing/outputs/conus/retro" \
    --start "$year-01-01" --end "$year-12-31" --workers 4
