#!/usr/bin/env python3
"""Convert one nested monthly Stage-IV GRIB1 tar archive."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.download.stage4_legacy import LegacyStage4Converter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--grid-template", required=True, type=Path)
    parser.add_argument("--keep-hourly", action="store_true")
    args = parser.parse_args()
    converted, skipped = LegacyStage4Converter(
        load_settings(), args.grid_template
    ).convert_month(args.archive, delete_hourly=not args.keep_hourly)
    print(f"Complete: {args.archive} ({converted} converted days, {skipped} skipped days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
