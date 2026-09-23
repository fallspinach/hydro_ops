"""Filter existing publications after a pilot, coordinating with ongoing recovery."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-job", required=True)
    parser.add_argument("--recovery-job", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    groups = {"historical": [], "post2020": []}
    for stream in ("retro", "nrt"):
        for path in sorted((settings.project_root / "forcing/outputs/conus" / stream / "hourly").glob("*/*/*.LDASIN_DOMAIN1")):
            groups["historical" if path.name[:8] < "20201014" else "post2020"].append(str(path))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    for label, paths in groups.items():
        batches = [paths[index:index + 31] for index in range(0, len(paths), 31)]
        dependency = f"afterok:{args.pilot_job}"
        if label == "post2020":
            dependency += f",afterany:{args.recovery_job}"
        print(json.dumps({"group": label, "days": len(paths), "tasks": len(batches),
                          "dependency": dependency}), flush=True)
        if args.dry_run or not batches:
            continue
        tasks = settings.work_root / f"inactive-mask-{stamp}-{label}.jsonl"
        tasks.write_text("".join(json.dumps(batch) + "\n" for batch in batches))
        command = ["sbatch", "--parsable", f"--partition={settings.slurm_partition}",
                   f"--dependency={dependency}", f"--array=0-{len(batches)-1}%4",
                   "--nodes=1", "--ntasks=1", "--cpus-per-task=12", "--tmp=120000",
                   "--time=24:00:00", f"--job-name=nwm-inactive-mask-{label}",
                   f"--output={settings.log_root}/inactive-mask-%A_%a.out",
                   (f"--export=ALL,HYDRO_OPS_PROJECT_ROOT={settings.project_root},"
                    f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_INACTIVE_MASK_TASKS={tasks}"),
                   "--wrap", f"{sys.executable} slurm/mask_published_forcing.py"]
        if settings.slurm_account:
            command.insert(1, f"--account={settings.slurm_account}")
        result = subprocess.run(command, cwd=settings.project_root, check=True,
                                text=True, capture_output=True)
        print(f"{label}_job={result.stdout.strip()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
