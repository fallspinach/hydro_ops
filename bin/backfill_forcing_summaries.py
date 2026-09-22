"""Bounded parallel daily summaries followed by monthly reuse; audited retro only."""

import argparse
import fcntl
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.temporal_summary import input_path, periods, summarize, summary_path


def audited(path):
    """Match an accepted historical static-envelope audit to the current inode."""
    manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text())
    envelope = manifest["static_envelope"]
    audit = json.loads(Path(envelope["audit"]).read_text())
    stat = path.stat()
    identity = {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if not (
        manifest.get("verified") is True
        and envelope.get("policy") == "nldas2_seven_met_static_envelope_v4"
        and envelope.get("published_identity") == identity
        and audit.get("published_identity") == identity
        and audit.get("status") == "published"
        and envelope.get("active_values_unchanged") is True
        and envelope.get("retained_values_unchanged") is True
        and envelope.get("valid_outside_mask") == 0
        and audit.get("candidate_sha256") == envelope.get("file_sha256")
        and bool(envelope.get("file_sha256"))
    ):
        raise ValueError(f"Missing/stale/unaccepted source audit: {path}")
    return identity


def reduce_day(task):
    root, output, day, reducers, names, units = task
    stop = day + timedelta(days=1)
    inputs = [input_path(root, d) for d in (day, stop)]
    identities = [audited(p) for p in inputs]
    started = time.monotonic()
    result = summarize(
        root,
        summary_path(output / "daily", day),
        day,
        stop,
        reducers,
        names,
        units,
        skip_existing=True,
        domain="conus",
        stream="retro",
    )
    if [audited(p) for p in inputs] != identities:
        raise ValueError(f"Source changed during summary: {day}")
    return {**result, "day": str(day), "seconds": time.monotonic() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4, 8), default=4)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    # Whole-month requests ensure monthly products are never silently partial.
    months = list(periods(args.start, args.end, "monthly"))
    days = [d for d, _ in periods(args.start, args.end, "daily")]
    for d in [*days, args.end + timedelta(days=1)]:
        audited(input_path(args.input_root, d))
    print(
        json.dumps(
            {
                "status": "source_audits_passed",
                "days": len(days),
                "boundary_day": str(args.end + timedelta(days=1)),
            }
        ),
        flush=True,
    )
    if args.audit_only:
        return
    args.output_root.mkdir(parents=True, exist_ok=True)
    # A cooperative single-controller lock also protects overlapping year requests.
    with (args.output_root / ".summary-backfill.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = Path(__file__).resolve().parents[1] / "config/forcing_daily_reducers.toml"
        reducers, names, units = load_forcing_reducers(config)
        started = time.monotonic()
        with ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            for result in pool.map(
                reduce_day,
                [(args.input_root, args.output_root, d, reducers, names, units) for d in days],
            ):
                print(json.dumps(result), flush=True)
        daily_seconds = time.monotonic() - started
        for start, stop in months:
            result = summarize(
                args.output_root / "daily",
                summary_path(args.output_root / "monthly", start, monthly=True),
                start,
                stop,
                reducers,
                names,
                units,
                from_daily=True,
                skip_existing=True,
                domain="conus",
                stream="retro",
            )
            print(json.dumps(result), flush=True)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "workers": args.workers,
                    "days": len(days),
                    "daily_seconds": daily_seconds,
                    "total_seconds": time.monotonic() - started,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
