#!/usr/bin/env python3
"""Generate conservative NWM-to-PRISM weights used by daily reconciliation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.inventory import netcdf_grid_fingerprint
from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    build_nwm_to_prism_weight_command,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nwm-grid", required=True, type=Path)
    parser.add_argument(
        "--nwm-scrip",
        required=True,
        type=Path,
        help="masked NWM SCRIP grid; its grid_imask excludes inactive rectangular cells",
    )
    parser.add_argument("--prism-grid", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cdo", default="cdo")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    executable = shutil.which(args.cdo)
    if executable is None:
        parser.error(f"CDO executable not found: {args.cdo}")
    if args.output.exists() and not args.force:
        raise FileExistsError(f"output exists; use --force to replace it: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f"{args.output.stem}.part{args.output.suffix}")
    partial.unlink(missing_ok=True)
    command = build_nwm_to_prism_weight_command(
        executable,
        args.nwm_grid,
        args.prism_grid,
        partial,
        nwm_scrip=args.nwm_scrip,
    )
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        operator = ConservativeOperator.from_cdo(partial)
        with Dataset(args.nwm_grid) as grid:
            active_domain = np.asarray(grid["active_domain"][:], dtype=bool)
        inactive_links = operator.inactive_link_count(active_domain)
        if inactive_links:
            raise RuntimeError(
                f"Generated reverse operator contains {inactive_links} inactive NWM links"
            )
        partial.replace(args.output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    manifest = args.output.with_suffix(f"{args.output.suffix}.manifest.json")
    metadata = {
        "created": datetime.now(UTC).isoformat(),
        "method": "conservative",
        "direction": "nwm_to_prism",
        "nwm_grid": str(args.nwm_grid),
        "nwm_scrip": str(args.nwm_scrip),
        "nwm_grid_fingerprint": netcdf_grid_fingerprint(
            args.nwm_grid, ("lat", "lon", "lat_bnds", "lon_bnds", "active_domain")
        ),
        "prism_grid": str(args.prism_grid),
        "prism_grid_fingerprint": netcdf_grid_fingerprint(args.prism_grid, ("lat", "lon")),
        "source_size": operator.source_size,
        "target_size": operator.target_size,
        "link_count": int(operator.weight.size),
        "inactive_source_link_count": inactive_links,
        "command": command,
        "cdo_stderr": completed.stderr,
    }
    manifest_partial = manifest.with_name(f"{manifest.name}.part")
    manifest_partial.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    manifest_partial.replace(manifest)
    print(args.output)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
