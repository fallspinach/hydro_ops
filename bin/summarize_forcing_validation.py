#!/usr/bin/env python3
"""Aggregate forcing-validation reports into one atomic promotion-gate ledger."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=("A", "B", "C", "D"))
    parser.add_argument("--reports", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = []
    for path in args.reports:
        report = json.loads(path.read_text())
        reports.append(
            {
                "report": str(path),
                "path": report.get("path"),
                "accepted": report.get("accepted") is True,
                "issues": report.get("issues", []),
                "context": report.get("context", {}),
            }
        )
    ledger = {
        "gate": args.gate,
        "created": datetime.now(UTC).isoformat(),
        "accepted": bool(reports) and all(report["accepted"] for report in reports),
        "report_count": len(reports),
        "accepted_count": sum(report["accepted"] for report in reports),
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".part")
    partial.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    partial.replace(args.output)
    print(json.dumps(ledger, indent=2, sort_keys=True))
    return 0 if ledger["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
