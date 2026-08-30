#!/usr/bin/env python3
#SBATCH --job-name=forcing-node
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --time=24:00:00
"""Use one full node for a bounded share of complete hourly forcing production."""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

requested_python = os.environ.get("HYDRO_OPS_PYTHON")
if requested_python and Path(sys.executable).resolve() != Path(requested_python).resolve():
    os.execv(requested_python, [requested_python, __file__, *sys.argv[1:]])

from hydro_ops.forcing.hybrid import HybridWeights
from hydro_ops.forcing.operations import OperationalLayout, produce_complete_hour
from hydro_ops.forcing.streams import baseline_root


def hours(start: datetime, end: datetime) -> list[datetime]:
    result = []
    current = start
    while current <= end:
        result.append(current)
        current += timedelta(hours=1)
    return result


def produce(arguments: tuple[datetime, Path, Path, Path]) -> dict:
    valid, project, output_root, scratch_root = arguments
    output = output_root / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
    try:
        return produce_complete_hour(
            valid,
            OperationalLayout.project_defaults(project),
            output,
            work_directory=scratch_root / valid.strftime("%Y%m%d%H"),
            hybrid_weights=HybridWeights(),
        )
    except Exception as error:  # noqa: BLE001 - isolate one failed hour from the node batch
        return {
            "valid_time": valid.isoformat(),
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }


def main() -> int:
    start = datetime.strptime(os.environ["HYDRO_OPS_START"], "%Y%m%d%H").replace(tzinfo=UTC)
    end = datetime.strptime(os.environ["HYDRO_OPS_END"], "%Y%m%d%H").replace(tzinfo=UTC)
    node_index = int(os.environ["SLURM_ARRAY_TASK_ID"])
    node_count = int(os.environ["HYDRO_OPS_NODE_COUNT"])
    workers = int(os.environ.get("HYDRO_OPS_WORKERS_PER_NODE", "16"))
    project = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"]).resolve()
    output_root = baseline_root(project)
    scratch_root = Path(
        f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}"
        "/forcing-production"
    )
    assigned = hours(start, end)[node_index::node_count]
    work = [(valid, project, output_root, scratch_root) for valid in assigned]
    failures = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(produce, work):
            print(json.dumps(result, sort_keys=True), flush=True)
            failures += result["status"] == "failed"
    print(
        json.dumps(
            {
                "node_index": node_index,
                "assigned_hours": len(assigned),
                "failures": failures,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
