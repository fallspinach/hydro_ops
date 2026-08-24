#!/usr/bin/env python3
"""Inventory NWM operational domain files for WRF-Hydro compatibility."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hydro_ops.nwm_domain import inventory, write_inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("data/static/nwm/operational/nwm.v3.1.6")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/inventory/nwm.v3.1.6-domain.json")
    )
    args = parser.parse_args()
    report = inventory(args.input)
    write_inventory(report, args.output)
    summary = report["summary"]
    print(
        f"domain inventory: compatible={summary['compatible']} "
        f"incompatible={summary['incompatible']} pending={summary['pending']}"
    )
    print(f"report: {args.output}")
    if summary["incompatible"]:
        return 2
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
