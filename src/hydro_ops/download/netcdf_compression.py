"""Atomic NetCDF4 compression for GRIB conversion products."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

_NETCDF_LOCK = threading.RLock()


def is_compressed_netcdf(path: Path) -> bool:
    """Return whether every array-valued data variable uses DEFLATE."""
    try:
        with path.open("rb") as stream:
            # Classic and 64-bit-offset NetCDF3 cannot contain compressed variables.
            if stream.read(4) in {b"CDF\x01", b"CDF\x02"}:
                return False
        with _NETCDF_LOCK, Dataset(path) as dataset:
            arrays = [variable for variable in dataset.variables.values() if variable.ndim >= 2]
            return bool(arrays) and all(
                bool(filters := variable.filters()) and filters.get("zlib", False)
                for variable in arrays
            )
    except (OSError, RuntimeError):
        return False


def _same_schema(source: Path, destination: Path) -> bool:
    """Check the lossless structural contract produced by nccopy."""
    try:
        with _NETCDF_LOCK, Dataset(source) as left, Dataset(destination) as right:
            if {name: len(dim) for name, dim in left.dimensions.items()} != {
                name: len(dim) for name, dim in right.dimensions.items()
            }:
                return False
            if set(left.variables) != set(right.variables):
                return False
            return all(
                left[name].dtype == right[name].dtype
                and left[name].dimensions == right[name].dimensions
                for name in left.variables
            )
    except (OSError, RuntimeError):
        return False


def _data_digest(path: Path) -> str:
    """Hash unscaled stored values in bounded slices."""
    digest = hashlib.sha256()
    with _NETCDF_LOCK, Dataset(path) as dataset:
        dataset.set_auto_maskandscale(False)
        for name in sorted(dataset.variables):
            variable = dataset[name]
            digest.update(name.encode())
            digest.update(variable.dtype.str.encode())
            if variable.ndim == 0:
                digest.update(np.asarray(variable[...]).tobytes())
                continue
            step = max(1, min(256, variable.shape[0]))
            for start in range(0, variable.shape[0], step):
                digest.update(np.ascontiguousarray(variable[start : start + step]).tobytes())
    return digest.hexdigest()


def compress_netcdf(
    source: Path,
    destination: Path,
    *,
    level: int = 2,
    nccopy: str = "nccopy",
    preserve_mtime: bool = True,
    work_directory: Path | None = None,
) -> None:
    """Write a compressed NetCDF4 copy and validate it before atomic publication."""
    executable = shutil.which(nccopy)
    if not executable:
        raise RuntimeError(f"NetCDF compression tool not found: {nccopy}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    work_root = destination.parent if work_directory is None else work_directory
    work_root.mkdir(parents=True, exist_ok=True)
    publishing = destination.with_name(f"{destination.name}.compressing")
    publishing.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="hydro_ops_netcdf_", dir=work_root) as temporary:
        partial = Path(temporary) / destination.name
        try:
            subprocess.run(
                [executable, "-k", "nc4", "-d", str(level), "-s", str(source), str(partial)],
                check=True,
                capture_output=True,
                text=True,
            )
            if (
                not is_compressed_netcdf(partial)
                or not _same_schema(source, partial)
                or _data_digest(source) != _data_digest(partial)
            ):
                raise RuntimeError(f"Compressed NetCDF validation failed: {source}")
            shutil.copyfile(partial, publishing)
            if publishing.stat().st_size != partial.stat().st_size:
                raise RuntimeError(f"Compressed NetCDF publication copy failed: {destination}")
            if preserve_mtime:
                modified = source.stat().st_mtime
                os.utime(publishing, (modified, modified))
            publishing.replace(destination)
        except Exception:
            publishing.unlink(missing_ok=True)
            raise


def convert_grib_with_wgrib2(
    executable: str,
    source: Path,
    destination: Path,
    *,
    level: int = 2,
    work_directory: Path | None = None,
) -> None:
    """Convert GRIB to temporary NetCDF3, then publish compressed NetCDF4."""
    work_root = destination.parent if work_directory is None else work_directory
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hydro_ops_wgrib2_", dir=work_root) as temporary:
        raw = Path(temporary) / f"{destination.name}.wgrib2.nc"
        subprocess.run(
            [executable, str(source), "-netcdf", str(raw)],
            check=True,
            capture_output=True,
            text=True,
        )
        compress_netcdf(
            raw,
            destination,
            level=level,
            preserve_mtime=False,
            work_directory=Path(temporary),
        )
