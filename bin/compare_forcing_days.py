#!/usr/bin/env python3
"""Compare all scientific fields in two daily forcing files with CDO."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    executable = shutil.which("cdo")
    if executable is None:
        raise RuntimeError("CDO is required for full-field comparison")
    completed = subprocess.run(
        [executable, "-s", "diffn", str(args.reference), str(args.candidate)],
        check=False,
        capture_output=True,
        text=True,
    )
    differences = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    report = {
        "scenario": args.scenario,
        "reference": str(args.reference),
        "candidate": str(args.candidate),
        "accepted": completed.returncode == 0 and not differences,
        "returncode": completed.returncode,
        "differences": differences,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    partial = args.report.with_suffix(args.report.suffix + ".part")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
