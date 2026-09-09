#!/usr/bin/env python3
"""Rasterize one official NWS RFC polygon onto curvilinear grid-cell centers."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from shapely import intersects_xy
from shapely.geometry import shape


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_feature(shapefile: Path, basin_id: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "GeoJSON",
            "/vsistdout/",
            str(shapefile),
            "-where",
            f"BASIN_ID = '{basin_id}'",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    collection = json.loads(completed.stdout)
    features = collection["features"]
    if len(features) != 1:
        raise ValueError(f"expected one {basin_id} feature, found {len(features)}")
    return features[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shapefile", required=True, type=Path)
    parser.add_argument("--source-zip", required=True, type=Path)
    parser.add_argument("--grid", "--stage4", dest="grid", required=True, type=Path)
    parser.add_argument("--latitude-variable", default="latitude")
    parser.add_argument("--longitude-variable", default="longitude")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--basin-id", default="CNRFC")
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()

    feature = read_feature(args.shapefile, args.basin_id)
    geometry = shape(feature["geometry"])
    with Dataset(args.grid) as source:
        latitude = np.asarray(source[args.latitude_variable][:], dtype=np.float64)
        longitude = np.asarray(source[args.longitude_variable][:], dtype=np.float64)
        x = np.asarray(source["x"][:], dtype=np.float64)
        y = np.asarray(source["y"][:], dtype=np.float64)
        x_attributes = {name: source["x"].getncattr(name) for name in source["x"].ncattrs()}
        y_attributes = {name: source["y"].getncattr(name) for name in source["y"].ncattrs()}
    if latitude.shape != longitude.shape or latitude.shape != (len(y), len(x)):
        raise ValueError("Grid coordinates have inconsistent dimensions")

    polygon_longitude = np.where(longitude > 180.0, longitude - 360.0, longitude)
    mask = intersects_xy(geometry, polygon_longitude, latitude)
    if not np.any(mask):
        raise ValueError("RFC rasterization produced an empty mask")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    temporary.unlink(missing_ok=True)
    with Dataset(temporary, "w", format="NETCDF4") as output:
        output.createDimension("y", len(y))
        output.createDimension("x", len(x))
        x_variable = output.createVariable("x", "f8", ("x",))
        y_variable = output.createVariable("y", "f8", ("y",))
        latitude_variable = output.createVariable(
            "latitude", "f8", ("y", "x"), zlib=True, complevel=2, shuffle=True
        )
        longitude_variable = output.createVariable(
            "longitude", "f8", ("y", "x"), zlib=True, complevel=2, shuffle=True
        )
        mask_variable = output.createVariable(
            "cnrfc_mask", "u1", ("y", "x"), zlib=True, complevel=2, shuffle=True
        )
        x_variable[:] = x
        y_variable[:] = y
        latitude_variable[:] = latitude
        longitude_variable[:] = longitude
        mask_variable[:] = mask.astype(np.uint8)
        x_variable.setncatts(x_attributes)
        y_variable.setncatts(y_attributes)
        latitude_variable.setncatts({"standard_name": "latitude", "units": "degrees_north"})
        longitude_variable.setncatts({"standard_name": "longitude", "units": "degrees_east"})
        mask_variable.setncatts(
            {
                "long_name": "California-Nevada River Forecast Center grid-center mask",
                "flag_values": np.asarray([0, 1], dtype=np.uint8),
                "flag_meanings": "outside_cnrfc inside_cnrfc",
                "coordinates": "latitude longitude",
            }
        )
        output.setncatts(
            {
                "Conventions": "CF-1.8",
                "title": f"{args.basin_id} responsibility area on {args.grid.name}",
                "basin_id": args.basin_id,
                "rasterization_rule": "grid cell center intersects official RFC polygon",
                "source_url": args.source_url,
                "source_zip": args.source_zip.name,
                "source_zip_md5": md5(args.source_zip),
                "source_shapefile": args.shapefile.name,
                "source_grid": str(args.grid),
                "source_latitude_variable": args.latitude_variable,
                "source_longitude_variable": args.longitude_variable,
                "source_feature_properties": json.dumps(feature["properties"], sort_keys=True),
                "created_utc": datetime.now(UTC).isoformat(),
            }
        )
    temporary.replace(args.output)
    print(f"output={args.output}")
    print(f"shape={mask.shape[0]}x{mask.shape[1]}")
    print(f"inside_cells={np.count_nonzero(mask)}")
    print(f"inside_fraction={np.mean(mask):.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
