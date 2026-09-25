#!/usr/bin/env bash
#SBATCH --job-name=forcing-conus-retro-summary-1991-202602-128cpu
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --array=0-35%16
#SBATCH --time=48:00:00
#SBATCH --output=forcing/logs/summary-1991-2026-%A_%a.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
index=${SLURM_ARRAY_TASK_ID:?}
if (( index < 0 || index > 35 )); then
    echo "Unexpected year index: $index" >&2
    exit 2
fi
year=$((1991 + index))
end="$year-12-31"
if (( year == 2026 )); then end=2026-02-28; fi
echo "CONUS retro summaries: $year-01-01 through $end; workers=4; allocated_cpus=8"
"$HYDRO_OPS_PYTHON" "$root/bin/backfill_forcing_summaries.py" \
    --input-root "$root/forcing/outputs/conus/retro/hourly" \
    --output-root "$root/forcing/outputs/conus/retro" \
    --start "$year-01-01" --end "$end" --workers 4 --parallel-years
