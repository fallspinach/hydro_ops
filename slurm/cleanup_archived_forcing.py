#!/usr/bin/env python3
#SBATCH --job-name=forcing-cleanup
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=24:00:00
"""Remove forcing artifacts protected by verified daily archives."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    return subprocess.run(
        [python, "bin/cleanup_archived_forcing.py", "--apply", *sys.argv[1:]], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
