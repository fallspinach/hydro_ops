# Source this from an entry point; do not derive roots from SLURM spool scripts.
# Site files are trusted executable shell configuration, not untrusted data.
project=${HYDRO_OPS_PROJECT_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}
if [[ "$project" != /* || ! -f "$project/config/project.toml" ]]; then
    echo "Invalid HYDRO_OPS_PROJECT_ROOT: expected an absolute project directory" >&2
    return 1
fi
source "$project/config/site.env"
if [[ -f "$project/config/site.local.env" ]]; then
    source "$project/config/site.local.env"
fi
env_bin=${HYDRO_OPS_ENV_BIN:?}
slurm_root=${HYDRO_OPS_SLURM_ROOT:?}
export HYDRO_OPS_PROJECT_ROOT="$project"
export HYDRO_OPS_PYTHON="$env_bin/python"
export PATH="$env_bin:$slurm_root/bin:$slurm_root/sbin:/usr/bin:/bin"
export SLURM_CONF=${HYDRO_OPS_SLURM_CONF:?}
export CMD_WLM_CLUSTER_NAME=${HYDRO_OPS_CLUSTER:?}
export LD_LIBRARY_PATH="$slurm_root/lib64:$slurm_root/lib64/slurm"
export PYTHONPATH="$project/src"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
