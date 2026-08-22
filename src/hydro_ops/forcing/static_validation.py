"""Validate and fingerprint terrain files used by forcing production."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


@dataclass(frozen=True)
class StaticValidation:
    source_elevation_fingerprint: str
    target_elevation_fingerprint: str


def file_sha256(path: Path, *, block_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _validate_elevation(
    path: Path,
    variable: str,
    expected_shape: tuple[int, int],
    *,
    active: np.ndarray | None = None,
) -> None:
    with Dataset(path) as dataset:
        if variable not in dataset.variables:
            raise ValueError(f"Elevation file {path} is missing {variable}")
        field = dataset[variable]
        shape = tuple(size for size in field.shape if size != 1)
        if shape != expected_shape:
            raise ValueError(
                f"Elevation {path}:{variable} shape {shape} differs from {expected_shape}"
            )
        values = np.ma.asarray(field[:]).squeeze()
        invalid = np.ma.getmaskarray(values) | ~np.isfinite(values.filled(np.nan))
        required = ~invalid if active is None else active
        if active is not None and np.any(invalid & required):
            raise ValueError(f"Elevation {path}:{variable} is missing required cells")
        finite = values.filled(np.nan)[required & ~invalid]
        if finite.size == 0:
            raise ValueError(f"Elevation {path}:{variable} has no finite cells")
        if np.any((finite < -500.0) | (finite > 9000.0)):
            raise ValueError(f"Elevation {path}:{variable} has implausible values")


def validate_terrain_bundle(
    source_elevation: Path,
    source_variable: str,
    source_shape: tuple[int, int],
    target_grid: Path,
    target_elevation: Path,
) -> StaticValidation:
    """Validate terrain coverage/ranges and return immutable content identities."""
    with Dataset(target_grid) as grid:
        target_shape = (len(grid.dimensions["y"]), len(grid.dimensions["x"]))
        active = np.asarray(grid["active_domain"][:], dtype=bool)
    _validate_elevation(source_elevation, source_variable, source_shape)
    _validate_elevation(
        target_elevation, "elevation", target_shape, active=active
    )
    return StaticValidation(file_sha256(source_elevation), file_sha256(target_elevation))
