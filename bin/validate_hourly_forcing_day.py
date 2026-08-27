#!/usr/bin/env python3
"""Fully validate one day of separately published hourly NWM forcing files."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing.stream_validation import validate_hourly_forcing_day


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("--scenario", default="hourly-baseline-postproduction")
    parser.add_argument("--slurm-job-id")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = validate_hourly_forcing_day(args.root, args.day)
    report["validated_at"] = datetime.now(UTC).isoformat()
    report["context"] = {
        "scenario": args.scenario,
        "stream": "baseline",
        "slurm_job_id": args.slurm_job_id,
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip(),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    partial = args.report.with_suffix(args.report.suffix + ".part")
    partial.write_text(rendered)
    partial.replace(args.report)
    print(rendered, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
