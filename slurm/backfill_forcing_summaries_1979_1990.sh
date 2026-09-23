#!/usr/bin/env bash
#SBATCH --job-name=forcing-conus-retro-summary-1979-1980-1986-1990
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --array=0-6%7
#SBATCH --time=24:00:00
#SBATCH --output=forcing/logs/summary-backfill-%A_%a.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
years=(1979 1980 1986 1987 1988 1989 1990)
year=${years[${SLURM_ARRAY_TASK_ID:?}]}
start="$year-01-01"
extra=()
if [[ "$year" == 1979 ]]; then
    start=1979-01-02
    extra+=(--skip-incomplete-first-month)
fi
"$HYDRO_OPS_PYTHON" "$root/bin/backfill_forcing_summaries.py" \
    --input-root "$root/forcing/outputs/conus/retro" \
    --output-root "$root/forcing/outputs/conus/retro" \
    --start "$start" --end "$year-12-31" --workers 4 --parallel-years "${extra[@]}"
