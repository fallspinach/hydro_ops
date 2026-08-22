#!/usr/bin/env python3
"""Validate forcing NetCDF files and report their native-grid fingerprints."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.inventory import PRODUCT_VARIABLES, inspect_forcing_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", required=True, choices=tuple(PRODUCT_VARIABLES))
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    failed = False
    for path in args.files:
        inventory = inspect_forcing_file(path, args.product)
        print(inventory.to_json())
        failed |= not inventory.valid
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
