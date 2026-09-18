"""Isolated real-availability NRT cycle and immediate unchanged-repeat benchmark."""
import argparse
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import activation, configuration, day_path, identity, run_cycle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    plan = json.loads((campaign / "submission.json").read_text())
    root = Path(__file__).resolve().parents[1]
    if not activation(root):
        raise RuntimeError("GFS integration acceptance gate is closed")
    start, end = map(date.fromisoformat, (plan["start"], plan["end"]))
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    work.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "scope": "isolated actual source availability; no source overrides",
              "start": str(start), "end": str(end), "job_id": os.environ["SLURM_JOB_ID"]}
    target = campaign / "acceptance.json"
    _atomic_json(target, report)
    try:
        options = {"output_root": campaign / "nrt", "baseline_root": campaign / "baseline",
                   "state_root": campaign / "status"}
        first = run_cycle(root, work, start, end, datetime.now(UTC),
                          requested_at=plan["requested_at"], **options)
        _atomic_json(campaign / "first-cycle.json", first)
        report["first_cycle"] = first
        if first["status"] != "passed":
            raise RuntimeError("First operational cycle failed; see first-cycle.json")
        baseline_before = {str(p): identity(p) for p in options["baseline_root"].glob("*/*/*.LDASIN_DOMAIN1")}
        if plan.get("require_optimized_writers"):
            config = configuration(root)
            if (config.get("baseline_writer_profile") != "validated_source_chunks_v1"
                    or config.get("reconciliation_writer_profile") != "validated_chunks_reuse_v1"):
                raise RuntimeError("Expected both adopted writer profiles")
            receipts = [json.loads(p.read_text()) for p in options["baseline_root"].glob("*/*/*.nrt-receipt.json")]
            if not receipts or any(r.get("baseline_archive_writer") != "compressed_chunks" for r in receipts):
                raise RuntimeError("Optimized baseline writer did not execute for every supporting day")
            finals = [json.loads(p.read_text()) for p in options["output_root"].glob("*/*/*.nrt-receipt.json")]
            if not finals or any(not r.get("prism_constrained") or r.get("calendar_archive_writer") != "compressed_chunks"
                                 or r.get("prism_window_archive_writers") != ["compressed_chunks", "compressed_chunks"] for r in finals):
                raise RuntimeError("Expected optimized PRISM/calendar publication on every target day")
            report["optimized_writers"] = {"status": "passed", "baseline_days": len(receipts), "final_days": len(finals)}
        before = {d["day"]: identity(day_path(options["output_root"], date.fromisoformat(d["day"])))
                  for d in first["days"]}
        requested = datetime.now(UTC).isoformat()
        repeat = run_cycle(root, work, start, end, datetime.now(UTC), requested_at=requested, **options)
        _atomic_json(campaign / "repeat-cycle.json", repeat)
        report["repeat_cycle"] = repeat
        if repeat["status"] != "passed" or any(d["status"] != "unchanged" for d in repeat["days"]):
            raise RuntimeError("Repeat was not unchanged; inspect source fingerprints and delayed GFS cycles")
        if any(identity(day_path(options["output_root"], date.fromisoformat(day))) != previous
               for day, previous in before.items()):
            raise RuntimeError("Unchanged repeat altered a published file")
        if any(identity(Path(p)) != previous for p, previous in baseline_before.items()):
            raise RuntimeError("Unchanged repeat rebuilt a baseline")
        report["baseline_reuse"] = "passed"
        report.update(status="passed", actual_gfs_hours=sum(d["gfs_hours"] for d in first["days"]),
                      first_worker_under_hour=first["latency"]["worker_seconds"] < 3600,
                      first_end_to_end_under_hour=first["latency"]["launch_to_publication_seconds"] < 3600,
                      repeat_under_hour=repeat["latency"]["worker_seconds"] < 3600)
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        _atomic_json(target, report)
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
