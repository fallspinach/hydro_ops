"""Freeze the 1979–2002 retro inventory and submit the approved static clipping."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = project / "forcing/outputs/conus/retro/hourly"
    paths = []
    day = date(1979, 1, 1)
    absent = []
    while day <= date(2002, 12, 31):
        path = root / f"{day:%Y/%m/%Y%m%d}.LDASIN_DOMAIN1"
        if path.is_file():
            paths.append(str(path))
        else:
            absent.append(str(path))
        day += timedelta(days=1)
    if absent:
        raise ValueError(f"Inventory has missing files: {absent}")
    batches = [paths[i:i + 14] for i in range(0, len(paths), 14)]
    config = {"root": str(root), "mask": str(args.mask.resolve()), "files": len(paths),
              "years": dict(sorted(Counter(Path(p).parent.parent.name for p in paths).items())),
              "tasks": len(batches), "concurrency": args.concurrency,
              "fast": True, "writers": 2,
              "scope": "retro only, 1979-01-01 through 2002-12-31; baseline and post-2020 excluded"}
    print(json.dumps(config, indent=2), flush=True)
    if args.dry_run:
        return
    args.campaign.mkdir(parents=True, exist_ok=False)
    (args.campaign / "campaign.json").write_text(json.dumps(config, indent=2) + "\n")
    (args.campaign / "tasks.jsonl").write_text("".join(json.dumps(batch) + "\n" for batch in batches))
    command = ["sbatch", "--parsable", "--partition=shared-128", "--nodes=1", "--ntasks=1",
               "--cpus-per-task=12", "--tmp=120000", "--time=48:00:00",
               f"--array=0-{len(batches)-1}%{args.concurrency}",
               "--job-name=nwm-retro-static-envelope-v4-19790101-20021231",
               f"--output={project}/forcing/logs/static-envelope-v4-%A_%a.out",
               f"--export=ALL,STATIC_ENVELOPE_CAMPAIGN={args.campaign.resolve()}",
               "--wrap", f"{sys.executable} {project}/slurm/apply_static_forcing_mask.py"]
    result = subprocess.run(command, cwd=project, text=True, capture_output=True, check=True)
    (args.campaign / "submission.json").write_text(json.dumps({"job": result.stdout.strip(), "command": command}, indent=2) + "\n")
    print(result.stdout.strip(), flush=True)


if __name__ == "__main__":
    main()
