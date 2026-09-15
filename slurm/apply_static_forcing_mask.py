"""Bounded-retry, per-file-resumable historical static-envelope worker."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    project = Path(__file__).resolve().parents[1]
    campaign = Path(os.environ["STATIC_ENVELOPE_CAMPAIGN"])
    config = json.loads((campaign / "campaign.json").read_text())
    index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    paths = json.loads((campaign / "tasks.jsonl").read_text().splitlines()[index])
    scratch = Path("/scratch") / os.environ["SLURM_JOB_USER"] / f"job_{os.environ['SLURM_JOB_ID']}"
    scratch.mkdir(parents=True, exist_ok=True)
    def process(path):
        command = [sys.executable, str(project / "bin/apply_static_forcing_mask.py"),
                   "--path", path, "--mask", config["mask"], "--root", config["root"],
                   "--state", str(config.get('audit_root', campaign / "audit")), "--work", str(scratch)]
        if config.get('fast', False):
            command.append('--fast')
        for attempt in (1, 2):
            result = subprocess.run(command, cwd=project, check=False)
            print(json.dumps({"path": path, "attempt": attempt, "exit_code": result.returncode}), flush=True)
            if result.returncode == 0:
                break
        else:
            return path
        return None
    with ThreadPoolExecutor(max_workers=int(config.get('writers', 1))) as pool:
        failures = [path for path in pool.map(process, paths) if path is not None]
    summary = {"index": index, "paths": len(paths), "failures": failures,
               "job": os.environ["SLURM_JOB_ID"]}
    (campaign / f"batch-{index:04}.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
