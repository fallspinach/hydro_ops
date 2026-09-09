#!/usr/bin/env python3
"""Convert six-hour Stage-IV fields from one retained daily archive."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.download.stage4_legacy import LegacyStage4Converter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--grid-template", required=True, type=Path)
    args = parser.parse_args()
    selected, converted = LegacyStage4Converter(
        load_settings(), args.grid_template
    ).convert_daily_six_hour(args.archive)
    print(f"Complete: {args.archive} ({selected} selected, {converted} converted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
