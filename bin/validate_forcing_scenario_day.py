#!/usr/bin/env python3
"""Validate source routing across 24 hourly outputs from a controlled scenario."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

PRECIPITATION_SOURCE_IDS = {
    "mrms_pass2": 1,
    "mrms_pass1": 2,
    "stage4_archive": 3,
    "stage4_realtime": 4,
    "nldas2": 5,
    "hrrr": 6,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--expected-forcing-source", choices=("nldas2", "hrrr"))
    parser.add_argument(
        "--forbid-candidate", action="append", default=[], choices=tuple(PRECIPITATION_SOURCE_IDS)
    )
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    directory = args.root / args.day.strftime("%Y/%m/%d")
    files = sorted(directory.glob(f"{args.day:%Y%m%d}??.LDASIN_DOMAIN1"))
    issues: list[str] = []
    forcing_sources: Counter[str] = Counter()
    candidate_hours: Counter[str] = Counter()
    precipitation_counts: Counter[int] = Counter()
    if len(files) != 24:
        issues.append(f"found {len(files)} hourly files; expected 24")
    for path in files:
        manifest = path.with_suffix(path.suffix + ".manifest.json")
        if not manifest.is_file():
            issues.append(f"manifest missing: {path}")
            continue
        summary = json.loads(manifest.read_text())
        forcing_sources[str(summary.get("forcing_source"))] += 1
        candidate_hours.update(summary.get("precipitation_candidates", []))
        with Dataset(path) as data:
            values, counts = np.unique(data["precip_source_id"][0], return_counts=True)
            precipitation_counts.update(
                {int(value): int(count) for value, count in zip(values, counts, strict=True)}
            )
    if args.expected_forcing_source and forcing_sources != {args.expected_forcing_source: 24}:
        issues.append(
            f"forcing sources are {dict(forcing_sources)}; expected only "
            f"{args.expected_forcing_source}"
        )
    for product in args.forbid_candidate:
        if candidate_hours[product]:
            issues.append(f"forbidden candidate {product} appeared in {candidate_hours[product]} hours")
        source_id = PRECIPITATION_SOURCE_IDS[product]
        if precipitation_counts[source_id]:
            issues.append(
                f"forbidden product {product} supplied {precipitation_counts[source_id]} cells"
            )
    report = {
        "scenario": args.scenario,
        "day": args.day.isoformat(),
        "root": str(args.root),
        "accepted": not issues,
        "issues": issues,
        "hourly_file_count": len(files),
        "forcing_source_hours": dict(sorted(forcing_sources.items())),
        "candidate_hours": dict(sorted(candidate_hours.items())),
        "precipitation_source_counts": dict(sorted(precipitation_counts.items())),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    partial = args.report.with_suffix(args.report.suffix + ".part")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
