"""Refresh sources, then submit an isolated recent-NRT operational acceptance run."""
import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from hydro_ops.config import load_settings
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    root = settings.project_root
    requested = datetime.now(UTC)
    end = requested.date() - timedelta(days=2)
    plan = {"requested_at": requested.isoformat(), "start": str(end - timedelta(days=1)),
            "end": str(end), "cpus": 64, "scratch_mb": 240000,
            "scope": "two-day cold build followed by immediate unchanged repeat"}
    if not args.submit:
        print(json.dumps(plan, indent=2))
        return
    campaign = settings.work_root / f"nrt-operational-test-{requested:%Y%m%dT%H%M%S}"
    campaign.mkdir(parents=True, exist_ok=False)
    record = campaign / "submission.json"
    _atomic_json(record, plan)
    queue = subprocess.check_output(["squeue", "-u", getpass.getuser(), "-h", "-o", "%A|%j"], text=True)
    names = {f"{s}_download" for s in ("nldas2", "hrrr", "prism", "stage4", "mrms")}
    active = [line.split("|", 1)[0] for line in queue.splitlines() if line.split("|", 1)[1] in names]
    refresh = subprocess.run([sys.executable, "bin/update_forcing.py", "--repair-lookback-days", "14"],
                             cwd=root, text=True, capture_output=True, check=False)
    (campaign / "source-refresh.log").write_text(refresh.stdout + refresh.stderr)
    plan["source_refresh_returncode"] = refresh.returncode
    plan["source_jobs"] = sorted(set(active + re.findall(r"Submitted batch job (\d+)", refresh.stdout)))
    _atomic_json(record, plan)
    if refresh.returncode:
        raise RuntimeError(f"Source refresh submission failed: {campaign}")
    command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}",
               "--nodes=1", "--ntasks=1", "--cpus-per-task=64", "--tmp=240000", "--time=48:00:00",
               f"--job-name=nrt-operational-refresh-repeat-{plan['start']}-{plan['end']}",
               f"--chdir={root}", f"--output={settings.log_root}/nrt-operational-test-%j.out"]
    if settings.slurm_account:
        command.append(f"--account={settings.slurm_account}")
    if plan["source_jobs"]:
        command.append("--dependency=afterany:" + ":".join(plan["source_jobs"]))
    command.extend(["--wrap", f"{sys.executable} -u {root}/bin/test_nrt_operational_cycle.py --campaign {campaign}"])
    env = dict(os.environ)
    for key in ("HYDRO_OPS_RETRO_NEW_PRODUCTION", "HYDRO_OPS_PRECIPITATION_CACHE", "HYDRO_OPS_PROFILE_DIRECTORY",
                "HYDRO_OPS_ARCHIVE_CHUNKS", "HYDRO_OPS_NRT_REUSE_WINDOWS"):
        env.pop(key, None)
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               HYDRO_OPS_BENCH_FAST_MASK="0", HYDRO_OPS_BENCH_MULTIDAY="0",
               PYTHONPATH=str(root / "src"), PATH=str(sys.executable.rsplit("/", 1)[0]) + os.pathsep + env["PATH"])
    plan["command"] = command
    _atomic_json(record, plan)
    plan["job"] = subprocess.check_output(command, env=env, text=True).strip().split(";")[0]
    _atomic_json(record, plan)
    print(json.dumps({**plan, "campaign": str(campaign)}, indent=2))


if __name__ == "__main__":
    main()
