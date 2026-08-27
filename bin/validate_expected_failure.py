#!/usr/bin/env python3
"""Record an expected clean failure with no published forcing artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--expected-message", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    log = args.log.read_text() if args.log.is_file() else ""
    published = sorted(
        str(path)
        for path in args.output_root.rglob("*")
        if path.is_file() and not path.name.endswith(".part")
    ) if args.output_root.exists() else []
    partials = sorted(str(path) for path in args.output_root.rglob("*.part")) if (
        args.output_root.exists()
    ) else []
    issues = []
    if args.expected_message not in log:
        issues.append(f"expected message is absent: {args.expected_message}")
    if published:
        issues.append(f"unexpected published files: {published}")
    if partials:
        issues.append(f"abandoned partial files: {partials}")
    report = {
        "scenario": args.scenario,
        "accepted": not issues,
        "issues": issues,
        "expected_message": args.expected_message,
        "log": str(args.log),
        "published_files": published,
        "partial_files": partials,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(args.report.suffix + ".part")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
