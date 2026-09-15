"""Isolated real-data mixed-hour, NLDAS replacement and PRISM acceptance gate."""

import json
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing import complete_day, nrt_cycle
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.source_selection import select_hourly_source


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ["SLURM_JOB_ID"]
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
    result = root / f"forcing/work/nrt-gfs-cycle-validation/job_{job}"
    result.mkdir(parents=True, exist_ok=False)
    engine = nrt_cycle.RecentNrt(root, work, datetime.now(UTC),
                               output_root=result / "nrt", baseline_root=result / "baseline")
    day = date(2026, 8, 25)
    report = {"policy": nrt_cycle.POLICY, "status": "running", "job_id": job,
              "scope": "isolated historical integration test, not an as-of availability reconstruction"}
    _atomic_json(result / "acceptance.json", report)

    def mixed_selection(valid, nldas, hrrr):
        preference = ("hrrr",) if valid.date() == day and valid.hour >= 12 else ("nldas2", "hrrr")
        return select_hourly_source(valid, nldas, hrrr, preference=preference)

    # Only this process's in-memory selectors change; source archives and queued
    # production jobs are untouched. Simulate a mid-day NLDAS arrival boundary.
    nrt_cycle.select_hourly_source = mixed_selection
    complete_day.select_hourly_source = mixed_selection
    try:
        path, first = engine.baseline_day(day)
    finally:
        nrt_cycle.select_hourly_source = select_hourly_source
        complete_day.select_hourly_source = select_hourly_source
    if first["gfs_hours"] != 12 or first["hourly_primary_sources"] != ["nldas2"] * 12 + ["hrrr"] * 12:
        raise ValueError("Mixed-hour GFS selection did not match the test boundary")
    shutil.copy2(path, result / "mixed-hour-baseline.LDASIN_DOMAIN1")
    report["mixed_hour"] = "passed"
    _atomic_json(result / "acceptance.json", report)
    _, replaced = engine.baseline_day(day)
    if replaced["gfs_hours"] != 0 or replaced["input_fingerprint"] == first["input_fingerprint"]:
        raise ValueError("Newly available NLDAS-2 did not replace GFS")
    report["nldas_replacement"] = "passed"
    report["prism"] = engine.produce_day(day)
    if not report["prism"]["prism_constrained"]:
        raise ValueError("Expected PRISM daily constraints in the integration test")
    report["repeat"] = engine.produce_day(day)
    if report["repeat"]["status"] != "unchanged":
        raise ValueError("Unchanged cycle unnecessarily rewrote NRT output")
    report.update(status="passed", finished_utc=datetime.now(UTC).isoformat(),
                  output=str(nrt_cycle.day_path(engine.output, day)))
    _atomic_json(result / "acceptance.json", report)
    activation = root / engine.config["activation_receipt"]
    activation.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(activation, {**report, "acceptance_report": str(result / "acceptance.json")})
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
