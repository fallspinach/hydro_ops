#!/usr/bin/env python3
"""Validate and record one complete daily NWM forcing collection."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.forcing.stream_validation import validate_daily_forcing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-revision", choices=("early", "provisional", "stable"))
    parser.add_argument("--scenario")
    parser.add_argument("--stream", choices=("baseline", "nrt", "retro"))
    parser.add_argument("--slurm-job-id")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate_daily_forcing(args.path, expected_revision=args.expected_revision)
    report["validated_at"] = datetime.now(UTC).isoformat()
    report["context"] = {
        "scenario": args.scenario,
        "stream": args.stream,
        "slurm_job_id": args.slurm_job_id,
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip(),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        partial = args.report.with_suffix(args.report.suffix + ".part")
        partial.write_text(rendered)
        partial.replace(args.report)
    print(rendered, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
