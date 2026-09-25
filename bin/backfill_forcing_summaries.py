"""Bounded parallel daily/monthly summaries from accepted retro or NRT files."""

import argparse
import fcntl
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.temporal_summary import input_path, periods, summarize, summary_path


def audited(path, stream="retro"):
    """Match an accepted historical static-envelope audit to the current inode."""
    if stream == "nrt":
        from hydro_ops.forcing.nrt_cycle import identity
        receipt = path.with_name(path.name + ".nrt-receipt.json")
        if not receipt.exists():
            # Earlier NRT publishers use the same strict static-envelope chain
            # as retro. Never fall back from an existing but failed/stale receipt.
            return audited(path, "retro")
        record = json.loads(receipt.read_text())
        current = identity(path)
        if (record.get("status") != "passed" or record.get("published_identity") != current
                or not record.get("sha256")):
            raise ValueError(f"Missing/stale/unaccepted NRT source audit: {path}")
        return current
    manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text())
    envelope = manifest["static_envelope"]
    audit = json.loads(Path(envelope["audit"]).read_text())
    stat = path.stat()
    identity = {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    # Historical post-2020 publishers kept the full audit bound to scratch,
    # then recorded a checksum-verified transfer to the permanent inode.
    # Follow that explicit chain; a generic identity mismatch is still rejected.
    staged = envelope.get("staged_identity")
    staged_transfer = (
        isinstance(staged, dict)
        and set(staged) == {"inode", "bytes", "mtime_ns"}
        and staged.get("bytes") == identity["bytes"]
        and audit.get("published_identity") == staged
        and envelope.get("audit_scope") in {
            "full staged-candidate content audit; permanent transfer checksum verified",
            "staged-candidate validation recorded in mask audit; permanent transfer checksum verified",
        }
        and bool(envelope.get("mask_sha256"))
        and audit.get("mask_sha256") == envelope["mask_sha256"]
    )
    # Early monthly-constrained publications predate the archive verified flag.
    # Accept their full, identity-matched field audit, never an explicit failure.
    fields = audit.get("fields", {})
    legacy_verified = "verified" not in manifest and all(
        fields.get(name, {}).get("records") == 24
        and fields[name].get("missing_active_cell_hours") == 0
        and fields[name].get("active_unchanged") is True
        and fields[name].get("retained_unchanged") is True
        and fields[name].get("valid_outside_mask") == 0
        and bool(fields[name].get("active_sha256"))
        for name in ("LWDOWN", "PSFC", "Q2D", "RAINRATE", "SWDOWN", "T2D", "U2D", "V2D")
    )
    if not (
        (manifest.get("verified") is True or legacy_verified)
        and envelope.get("policy") == "nldas2_seven_met_static_envelope_v4"
        and envelope.get("published_identity") == identity
        and (audit.get("published_identity") == identity or staged_transfer)
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
    root, output, day, reducers, names, units, *options = task
    stream = options[0] if options else "retro"
    stop = day + timedelta(days=1)
    inputs = [input_path(root, d) for d in (day, stop)]
    identities = [audited(p, stream) for p in inputs]
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
        replace_stale=stream == "nrt",
        domain="conus",
        stream=stream,
    )
    if [audited(p, stream) for p in inputs] != identities:
        raise ValueError(f"Source changed during summary: {day}")
    return {**result, "day": str(day), "seconds": time.monotonic() - started}


@contextmanager
def publication_locks(root, start, end, parallel_years=False):
    with ExitStack() as locks:
        lock = locks.enter_context((root / ".summary-backfill.lock").open("a"))
        mode = fcntl.LOCK_SH if parallel_years else fcntl.LOCK_EX
        fcntl.flock(lock, mode | fcntl.LOCK_NB)
        if parallel_years:
            for year in range(start.year, end.year + 1):
                year_lock = locks.enter_context(
                    (root / f".summary-backfill-{year}.lock").open("a")
                )
                fcntl.flock(year_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def requested_months(start, end, skip_incomplete_first_month=False):
    month_start = start
    if skip_incomplete_first_month and month_start.day != 1:
        month_start = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return list(periods(month_start, end, "monthly")) if month_start <= end else []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4, 8), default=4)
    parser.add_argument("--stream", choices=("retro", "nrt"), default="retro")
    parser.add_argument("--complete-months-only", action="store_true",
                        help="Allow partial-month date ranges; publish only fully included months")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--parallel-years", action="store_true",
                        help="Shared root lock plus exclusive year locks for disjoint campaigns")
    parser.add_argument("--skip-incomplete-first-month", action="store_true",
                        help="Produce daily records from start, but omit its incomplete monthly summary")
    args = parser.parse_args()
    # Whole-month requests ensure monthly products are never silently partial.
    if args.complete_months_only:
        from hydro_ops.forcing.temporal_summary import next_month
        first = args.start if args.start.day == 1 else next_month(args.start)
        months = []
        while next_month(first) <= args.end + timedelta(days=1):
            months.append((first, next_month(first)))
            first = next_month(first)
    else:
        months = requested_months(args.start, args.end, args.skip_incomplete_first_month)
    days = [d for d, _ in periods(args.start, args.end, "daily")]
    for d in [*days, args.end + timedelta(days=1)]:
        audited(input_path(args.input_root, d), args.stream)
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
    with publication_locks(args.output_root, args.start, args.end, args.parallel_years):
        config = Path(__file__).resolve().parents[1] / "config/forcing_daily_reducers.toml"
        reducers, names, units = load_forcing_reducers(config)
        started = time.monotonic()
        with ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            for result in pool.map(
                reduce_day,
                [(args.input_root, args.output_root, d, reducers, names, units, args.stream) for d in days],
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
                replace_stale=args.stream == "nrt",
                domain="conus",
                stream=args.stream,
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
