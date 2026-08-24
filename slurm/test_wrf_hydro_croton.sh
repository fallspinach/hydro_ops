#!/usr/bin/env bash
#SBATCH --job-name=wrfh-croton
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:15:00

set -euo pipefail

project_root=${SLURM_SUBMIT_DIR:?Submit this job from the hydro_ops project root}
run_dir="$project_root/external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run"
result_dir=${WRF_HYDRO_RESULT_DIR:-"$run_dir/output_nwm_ana_smoke"}

module purge
module load slurm/AWARE/23.02.7
module load cpu/0.21.2
module load intel/2023.2.4.31
module load intel-mpi/2021.14.2.9
module load netcdf-fortran/4.5.3

cd "$run_dir"
mkdir -p "$result_dir"
if compgen -G 'diag_hydro.*' >/dev/null || find "$result_dir" -mindepth 1 -print -quit | grep -q .; then
    echo "Smoke-test outputs already exist; use a fresh configured Run directory." >&2
    exit 2
fi

# Intel MPI's native launcher is required here. AWARE's SLURM PMI2 plugin corrupts the
# startup key/value exchange for this Intel MPI build.
mpiexec -n "$SLURM_NTASKS" ./wrf_hydro.exe 2>&1 | tee "$result_dir/model.stdout"

# v5.4.0 writes the completion sentinel to stdout; diag_hydro contains restart diagnostics.
success_count=$(grep -c 'The model finished successfully' "$result_dir/model.stdout")
if [[ "$success_count" -ne "$SLURM_NTASKS" ]]; then
    echo "Expected $SLURM_NTASKS successful rank diagnostics, found $success_count" >&2
    exit 1
fi

for pattern in \
    '*.CHANOBS_DOMAIN1' '*.CHRTOUT_DOMAIN1' '*.CHRTOUT_GRID1' \
    '*.GWOUT_DOMAIN1' '*.LAKEOUT_DOMAIN1' '*.LDASOUT_DOMAIN1' \
    '*.LSMOUT_DOMAIN1' '*.RTOUT_DOMAIN1' 'HYDRO_RST.*_DOMAIN1' \
    'RESTART.*_DOMAIN1' 'nudgingLastObs.*.nc' 'diag_hydro.*'; do
    for output in $pattern; do
        [[ -e "$output" ]] || continue
        mv "$output" "$result_dir/"
    done
done

echo "WRF-Hydro Croton NWM analysis smoke test passed with $success_count MPI ranks."
