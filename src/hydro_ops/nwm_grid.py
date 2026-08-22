"""Extract static NWM target-grid and SCRIP files from an LDASIN sample."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

GRID_VARIABLES = ("lon", "lat", "lon_bnds", "lat_bnds")


def _copy_attributes(source, destination) -> None:
    destination.setncatts({name: source.getncattr(name) for name in source.ncattrs()})


def _prepare_output(path: Path, force: bool) -> Path:
    if path.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.part")
    partial.unlink(missing_ok=True)
    return partial


def extract_target_grid(source_path: Path, output_path: Path, *, force: bool = False) -> None:
    """Create a compact CF file holding coordinates, cell corners, and active mask."""
    partial = _prepare_output(output_path, force)
    try:
        with Dataset(source_path) as source, Dataset(partial, "w", format="NETCDF4") as output:
            for name in (*GRID_VARIABLES, "T2D"):
                if name not in source.variables:
                    raise RuntimeError(f"Required variable missing from NWM sample: {name}")
            ny, nx = source.dimensions["y"].size, source.dimensions["x"].size
            corners = source.dimensions["nv4"].size
            if corners != 4:
                raise RuntimeError(f"Expected four NWM cell corners; found {corners}")
            output.createDimension("y", ny)
            output.createDimension("x", nx)
            output.createDimension("nv4", corners)
            output.setncatts(
                {
                    "Conventions": "CF-1.8",
                    "title": "National Water Model CONUS 1-km forcing target grid",
                    "source": str(source_path),
                    "history": f"{datetime.now(UTC).isoformat()} extracted by extract_nwm_grid.py",
                }
            )
            y_coordinate = output.createVariable("y", "i4", ("y",))
            x_coordinate = output.createVariable("x", "i4", ("x",))
            y_coordinate[:] = np.arange(ny, dtype=np.int32)
            x_coordinate[:] = np.arange(nx, dtype=np.int32)
            y_coordinate.setncatts({"long_name": "NWM grid row index"})
            x_coordinate.setncatts({"long_name": "NWM grid column index"})

            variables = {}
            for name in GRID_VARIABLES:
                original = source[name]
                chunks = (
                    (min(120, ny), min(288, nx), 4)
                    if original.ndim == 3
                    else (min(240, ny), min(288, nx))
                )
                variable = output.createVariable(
                    name,
                    original.dtype,
                    original.dimensions,
                    zlib=True,
                    complevel=4,
                    shuffle=True,
                    chunksizes=chunks,
                )
                _copy_attributes(original, variable)
                variables[name] = variable
            active = output.createVariable(
                "active_domain",
                "i1",
                ("y", "x"),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=(min(240, ny), min(288, nx)),
                fill_value=np.int8(-1),
            )
            active.setncatts(
                {
                    "long_name": "NWM forcing active-domain mask",
                    "flag_values": np.array([0, 1], dtype=np.int8),
                    "flag_meanings": "inactive active",
                    "coordinates": "lat lon",
                }
            )
            for start in range(0, ny, 120):
                stop = min(start + 120, ny)
                for name, variable in variables.items():
                    variable[start:stop, ...] = source[name][start:stop, ...]
                mask = np.ma.getmaskarray(source["T2D"][0, start:stop, :])
                active[start:stop, :] = (~mask).astype(np.int8)
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def create_scrip_grid(target_path: Path, output_path: Path, *, force: bool = False) -> None:
    """Flatten the target grid into the standard SCRIP grid-description schema."""
    partial = _prepare_output(output_path, force)
    try:
        with Dataset(target_path) as target, Dataset(partial, "w", format="NETCDF4") as output:
            ny, nx = target.dimensions["y"].size, target.dimensions["x"].size
            size = ny * nx
            output.createDimension("grid_size", size)
            output.createDimension("grid_corners", 4)
            output.createDimension("grid_rank", 2)
            output.setncatts(
                {
                    "title": "SCRIP description of the NWM CONUS 1-km forcing grid",
                    "Conventions": "SCRIP",
                    "source": str(target_path),
                    "history": f"{datetime.now(UTC).isoformat()} created by extract_nwm_grid.py",
                }
            )
            dimensions = output.createVariable("grid_dims", "i4", ("grid_rank",))
            dimensions[:] = np.array([nx, ny], dtype=np.int32)
            linear_chunk = (min(262144, size),)
            mask = output.createVariable(
                "grid_imask",
                "i4",
                ("grid_size",),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=linear_chunk,
            )
            center_lat = output.createVariable(
                "grid_center_lat",
                "f8",
                ("grid_size",),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=linear_chunk,
            )
            center_lon = output.createVariable(
                "grid_center_lon",
                "f8",
                ("grid_size",),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=linear_chunk,
            )
            corner_lat = output.createVariable(
                "grid_corner_lat",
                "f8",
                ("grid_size", "grid_corners"),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=(min(65536, size), 4),
            )
            corner_lon = output.createVariable(
                "grid_corner_lon",
                "f8",
                ("grid_size", "grid_corners"),
                zlib=True,
                complevel=4,
                shuffle=True,
                chunksizes=(min(65536, size), 4),
            )
            for variable in (center_lat, center_lon, corner_lat, corner_lon):
                variable.setncattr("units", "degrees")
            for start in range(0, ny, 120):
                stop = min(start + 120, ny)
                flat_start, flat_stop = start * nx, stop * nx
                mask[flat_start:flat_stop] = target["active_domain"][start:stop, :].reshape(-1)
                center_lat[flat_start:flat_stop] = target["lat"][start:stop, :].reshape(-1)
                center_lon[flat_start:flat_stop] = target["lon"][start:stop, :].reshape(-1)
                corner_lat[flat_start:flat_stop, :] = target["lat_bnds"][start:stop, :, :].reshape(
                    -1, 4
                )
                corner_lon[flat_start:flat_stop, :] = target["lon_bnds"][start:stop, :, :].reshape(
                    -1, 4
                )
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="sample NWM LDASIN NetCDF file")
    parser.add_argument("--target", type=Path, required=True, help="output CF target-grid file")
    parser.add_argument("--scrip", type=Path, required=True, help="output SCRIP grid file")
    parser.add_argument("--force", action="store_true", help="replace existing outputs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    extract_target_grid(args.source, args.target, force=args.force)
    create_scrip_grid(args.target, args.scrip, force=args.force)
    print(f"Created {args.target}")
    print(f"Created {args.scrip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
