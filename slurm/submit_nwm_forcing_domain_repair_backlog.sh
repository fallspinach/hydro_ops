#!/usr/bin/env bash
# Submit large domain-repair arrays as account job slots become available.

#SBATCH --job-name=nwm-domain-repair-backlog-1979-2002-content-audit
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=48:00:00
#SBATCH --output=forcing/logs/domain-repair-backlog-%j.out

set -euo pipefail

project_root=${HYDRO_OPS_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:?missing project root}}
cd "$project_root"
export PYTHONPATH="$project_root/src${PYTHONPATH:+:$PYTHONPATH}"
task_python=${HYDRO_OPS_PYTHON:-python3}

ranges=(
    1979-01-01:1980-12-31
    1981-01-01:1981-12-31
    1986-01-01:1987-12-31
    1988-01-01:1989-12-31
    1990-01-01:1991-12-31
    1992-01-01:1993-12-31
    1994-01-01:1995-12-31
    1996-01-01:1997-12-31
    1998-01-01:1999-12-31
    2000-01-01:2001-12-31
    2002-01-01:2002-12-31
)

for range in "${ranges[@]}"; do
    start=${range%%:*}
    end=${range##*:}
    while ! "$task_python" bin/submit_nwm_forcing_domain_repair.py \
        --root forcing/outputs/conus/retro/hourly \
        --start "$start" \
        --end "$end" \
        --project-root "$project_root" \
        --max-concurrent 4 \
        --include-all-existing; do
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) submission slots unavailable for $start:$end; retrying"
        sleep 60
    done
done
