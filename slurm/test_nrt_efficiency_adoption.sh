#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=240000
#SBATCH --time=04:00:00
#SBATCH --job-name=nrt-adopt-shared-precip-sparse-audit-full-cycle-repeat
#SBATCH --output=forcing/logs/nrt-efficiency-adoption-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
cd "$root"
export HYDRO_OPS_MIN_SCRATCH_FREE_GB=120
"$HYDRO_OPS_PYTHON" - <<'PY'
import os
from datetime import UTC, datetime
from pathlib import Path
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.retro_publication import check_scratch

root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
work.mkdir(parents=True, exist_ok=True)
check_scratch(work)
campaign = root / f'forcing/work/nrt-efficiency-adoption-{os.environ["SLURM_JOB_ID"]}'
campaign.mkdir(exist_ok=False)
_atomic_json(campaign / 'submission.json', {
    'start': '2026-09-20', 'end': '2026-09-20',
    'requested_at': datetime.now(UTC).isoformat(),
    'require_optimized_writers': True,
    'scope': 'private full PRISM cycle and unchanged repeat; no production writes',
})
PY
exec "$HYDRO_OPS_PYTHON" bin/test_nrt_operational_cycle.py \
    --campaign "$root/forcing/work/nrt-efficiency-adoption-${SLURM_JOB_ID}"
