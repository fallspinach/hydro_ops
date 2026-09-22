"""Read-only login-node cron preflight; never submits a SLURM job."""

import getpass
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys


def main():
    modules = ("hydro_ops.config", "requests", "numpy", "xarray", "netCDF4", "h5netcdf")
    for module in modules:
        importlib.import_module(module)
    from hydro_ops.config import load_settings

    settings = load_settings()
    commands = {}
    for name in ("squeue", "sbatch", "sacct", "python", "cdo", "ncks", "ncrcat"):
        path = shutil.which(name)
        if path is None:
            raise RuntimeError(f"Required executable unavailable: {name}")
        commands[name] = path
    for name in ("squeue", "sbatch", "sacct"):
        subprocess.run([name, "--version"], check=True, capture_output=True, text=True, timeout=30)
    query = subprocess.run(
        ["squeue", "--noheader", "--user", getpass.getuser(), "--format=%i"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "host": socket.gethostname(),
                "python": sys.executable,
                "project_root": str(settings.project_root),
                "slurm_conf": os.environ["SLURM_CONF"],
                "commands": commands,
                "imports": list(modules),
                "queue_records": len(query.stdout.splitlines()),
                "job_submitted": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
