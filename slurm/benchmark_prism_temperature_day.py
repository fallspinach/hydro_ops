#!/usr/bin/env python3
"""Benchmark one PRISM temperature-constrained day from complete hourly forcing."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def main() -> int:
    first = date.fromisoformat(os.environ["HYDRO_OPS_PRISM_FIRST_DAY"])
    day = first + timedelta(days=int(os.environ["SLURM_ARRAY_TASK_ID"]))
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    command = [
        sys.executable,
        str(project / "bin/produce_prism_constrained_day.py"),
        "--day",
        day.isoformat(),
        "--preliminary-root",
        os.environ["HYDRO_OPS_PRELIMINARY_ROOT"],
        "--complete-root",
        os.environ["HYDRO_OPS_COMPLETE_ROOT"],
        "--output-root",
        os.environ["HYDRO_OPS_OUTPUT_ROOT"],
        "--jobs",
        os.environ.get("HYDRO_OPS_PRISM_JOBS", "12"),
        "--force",
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
