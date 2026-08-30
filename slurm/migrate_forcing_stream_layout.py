#!/usr/bin/env python3
#SBATCH --job-name=forcing-layout-migration
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=06:00:00
"""Wait for forcing writers, then run the canonical stream-layout migration."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    python = os.environ.get("HYDRO_OPS_PYTHON", sys.executable)
    return subprocess.run(
        [python, str(project / "bin/migrate_forcing_stream_layout.py"), "--project-root", str(project)],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
