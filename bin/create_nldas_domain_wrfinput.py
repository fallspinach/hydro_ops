#!/usr/bin/env python3
"""Create a derived wrfinput whose active land is bounded by the NLDAS-2 rectangle."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.coverage import geographic_domain_mask


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        raise FileExistsError(f"Output exists; use --force to replace it: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.unlink(missing_ok=True)
    shutil.copy2(args.source, temporary)
    try:
        with Dataset(temporary, "r+") as data:
            latitude = np.asarray(data["XLAT"][:]).squeeze()
            longitude = np.asarray(data["XLONG"][:]).squeeze()
            inside = geographic_domain_mask(latitude, longitude)
            xland = np.asarray(data["XLAND"][:])
            original_land = xland == 1
            outside = ~inside
            if xland.ndim == 3:
                outside = outside[None, :, :]
            xland[outside] = 2
            data["XLAND"][:] = xland
            data.setncattr("forcing_domain_policy", "nldas2_rectangle_nearest_valid_v1")
            data.setncattr("forcing_domain_bounds", "25<=lat<=53,-125<=lon<=-67")
            data.setncattr("forcing_domain_deactivated_land_cells", int((original_land & outside).sum()))
        temporary.replace(args.output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
