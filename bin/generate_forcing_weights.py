#!/usr/bin/env python3
"""Generate direct source-to-NWM remapping weights and a provenance manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from hydro_ops.forcing.inventory import PRODUCT_VARIABLES
from hydro_ops.forcing.weights import OPERATORS, generate_weights


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--product", required=True, choices=tuple(PRODUCT_VARIABLES))
    parser.add_argument("--variable", required=True)
    parser.add_argument(
        "--cdo-source",
        type=Path,
        help="alternate native geometry source, such as HRRR GRIB for conservative weights",
    )
    parser.add_argument("--cdo-variable", help="variable name in --cdo-source")
    parser.add_argument("--target-grid", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--method", choices=tuple(OPERATORS), default="bilinear")
    parser.add_argument("--cdo", default="cdo")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    outputs = generate_weights(
        args.source,
        args.product,
        args.variable,
        args.target_grid,
        args.output,
        method=args.method,
        cdo=args.cdo,
        cdo_source=args.cdo_source,
        cdo_variable=args.cdo_variable,
        force=args.force,
    )
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
