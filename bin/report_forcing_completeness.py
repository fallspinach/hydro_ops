#!/usr/bin/env python3
"""Report missing NLDAS-2, HRRR, and PRISM timestamps over a date range."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date

from hydro_ops.config import load_settings
from hydro_ops.forcing.completeness import report_range

PRODUCTS = ("nldas2", "hrrr", "prism_tmin", "prism_tmax", "prism_tmean", "prism_ppt")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("dates must have format YYYY-MM-DD") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument("--product", action="append", choices=PRODUCTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    roots = {
        "nldas2": settings.nldas_data_dir,
        "hrrr": settings.hrrr_data_dir,
        **{product: settings.prism_data_dir for product in PRODUCTS if product.startswith("prism_")},
    }
    failed = False
    for product in args.product or PRODUCTS:
        reports = report_range(product, roots[product], args.start, args.end)
        incomplete = [report for report in reports if not report.complete]
        failed |= bool(incomplete)
        if args.json:
            print(
                json.dumps(
                    {
                        "product": product,
                        "start": args.start.isoformat(),
                        "end": args.end.isoformat(),
                        "complete_days": len(reports) - len(incomplete),
                        "expected_days": len(reports),
                        "incomplete": [asdict(report) for report in incomplete],
                    },
                    default=str,
                    sort_keys=True,
                )
            )
        else:
            print(
                f"{product:<12} {len(reports) - len(incomplete):>5}/{len(reports):<5} "
                f"complete days; {len(incomplete)} incomplete"
            )
            for report in incomplete:
                sample = ", ".join(report.missing[:4])
                suffix = " ..." if len(report.missing) > 4 else ""
                print(f"  {report.day}: {report.present}/{report.expected}; missing {sample}{suffix}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
