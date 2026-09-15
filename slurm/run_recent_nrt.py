"""Recent-NRT worker, submitted only after the coordinated external refresh."""

import os
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing.nrt_cycle import activation, run_cycle


def main():
    root = Path(os.environ["HYDRO_OPS_PROJECT_ROOT"])
    if not activation(root):
        raise RuntimeError("Recent NRT integration has not passed its activation test")
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    report = run_cycle(root, work, date.fromisoformat(os.environ["NRT_START"]),
                       date.fromisoformat(os.environ["NRT_END"]), datetime.now(UTC),
                       requested_at=os.environ.get("NRT_REQUESTED_AT"))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
