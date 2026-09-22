#!/usr/bin/env bash
# Explicit login-node environment; no Conda activation or interactive startup files.
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/project_environment.sh"

if (( $# == 0 )); then
    echo "Usage: bash bin/run_cron.sh <Python script and arguments> | --check" >&2
    exit 2
fi
test -x "$env_bin/python"
test -r "$SLURM_CONF"
cd "$project"
for tool in squeue sbatch sacct; do
    command -v "$tool" >/dev/null || {
        echo "Required command unavailable: $tool" >&2
        exit 1
    }
done
echo "$(date -u +%FT%TZ) host=$(hostname) cron_entry=$1" >&2
if [[ "$1" == --check ]]; then
    exec "$env_bin/python" bin/check_cron_environment.py
fi
exec "$env_bin/python" "$@"
