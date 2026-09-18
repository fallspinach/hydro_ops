"""Compare baseline daily writers on identical hourly inputs, then audit publication."""
import argparse
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from benchmark_nrt_reconciliation import compare
from netCDF4 import Dataset

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.gfs_publication import _atomic_json


def paired_archive(paths, destination, day, *, optimized_first=False, **kwargs):
    """Retain the optimized archive only after an independent decoded comparison."""
    before = [nrt_cycle.identity(p) for p in paths]
    # The experiment chooses both arms explicitly, independent of production defaults.
    kwargs.pop("chunk_copy", None)
    kwargs.pop("preserve_source_chunks", None)
    reference = destination.with_name("reference-" + destination.name)
    targets = {"reference": reference, "optimized": destination}
    order = ("optimized", "reference") if optimized_first else ("reference", "optimized")
    report = {"day": str(day), "order": order, "arms": {}}
    for mode in order:
        begin = time.monotonic()
        create_daily_archive(paths, targets[mode], day, chunk_copy=mode == "optimized",
                             preserve_source_chunks=mode == "optimized", **kwargs)
        elapsed = time.monotonic() - begin
        manifest = json.loads(targets[mode].with_name(targets[mode].name + ".manifest.json").read_text())
        report["arms"][mode] = {"seconds": elapsed, "bytes": targets[mode].stat().st_size,
                                "archive_writer": manifest.get("archive_writer", "value_based"),
                                "timing": manifest.get("timing")}
    if report["arms"]["optimized"]["archive_writer"] != "compressed_chunks":
        raise ValueError("Hourly encodings caused fallback; no chunk-writer speedup demonstrated")
    begin = time.monotonic()
    compare(reference, destination)
    report["independent_comparison_seconds"] = time.monotonic() - begin
    if before != [nrt_cycle.identity(p) for p in paths]:
        raise ValueError("Hourly input identity changed during paired test")
    report.update(status="passed", speedup=report["arms"]["reference"]["seconds"] /
                  report["arms"]["optimized"]["seconds"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.relative_to((root / "forcing/work").resolve())
    campaign.mkdir(parents=True, exist_ok=False)
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    result = {"status": "running", "job_id": os.environ["SLURM_JOB_ID"],
              "production_switch": False, "days": [], "assembly": []}
    _atomic_json(campaign / "acceptance.json", result)
    original = nrt_cycle.create_daily_archive

    def paired(paths, destination, day, **kwargs):
        with Dataset(paths[0]) as data:
            result.setdefault("input_encodings", {})[str(day)] = {
                name: {"dtype": str(var.dtype), "chunks": var.chunking(), "filters": var.filters()}
                for name, var in data.variables.items()}
        _atomic_json(campaign / "acceptance.json", result)
        report = paired_archive(paths, destination, day,
                                optimized_first=bool(result["assembly"]), **kwargs)
        result["assembly"].append(report)
        _atomic_json(campaign / "acceptance.json", result)
        return destination

    nrt_cycle.create_daily_archive = paired
    try:
        engine = nrt_cycle.RecentNrt(root, work, datetime.now(UTC),
                                    baseline_root=campaign / "baseline", output_root=campaign / "nrt")
        for day, expected_source in ((date(2026, 9, 10), "nldas2"), (date(2026, 9, 15), "hrrr")):
            selections, _ = nrt_cycle.baseline_plan(day, engine.layout, engine.config)
            if {s.product for s in selections} != {expected_source}:
                raise ValueError(f"Expected {expected_source} for {day}; actual source coverage changed")
            begin = time.monotonic()
            output, receipt = engine.baseline_day(day)
            result["days"].append({"day": str(day), "source": expected_source,
                                   "output": str(output), "status": receipt["status"],
                                   "gfs_hours": receipt["gfs_hours"],
                                   "instrumented_baseline_seconds": time.monotonic() - begin})
            _atomic_json(campaign / "acceptance.json", result)
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        nrt_cycle.create_daily_archive = original
        result["finished_utc"] = datetime.now(UTC).isoformat()
        _atomic_json(campaign / "acceptance.json", result)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
