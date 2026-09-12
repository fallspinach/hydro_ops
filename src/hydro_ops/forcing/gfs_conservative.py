"""Versioned CDO conservative precipitation weights for the sparse HRRR gap."""

import json
import os
import subprocess
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from scipy.sparse import csr_matrix

from hydro_ops.forcing.gfs_gap import sha


def build_conservative(geometry_path, target_grid, destination, work):
    if destination.exists():
        raise FileExistsError(destination)
    work.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with np.load(geometry_path) as geometry:
        indices = geometry["indices"]
        source_lat, source_lon = geometry["source_lat"], geometry["source_lon"]
        target = work / "gfs_gap_scrip.nc"
        source = work / "gfs_source_geometry.nc"
        with Dataset(target_grid) as grid, Dataset(target, "w") as dst:
            if tuple(grid["lat"].shape) != tuple(geometry["shape"]):
                raise ValueError("Target shape mismatch")
            # Published grid centers are float64; the model-derived envelope
            # stores float32. Compare values rather than hashes of unlike dtypes.
            np.testing.assert_allclose(np.asarray(grid["lat"][:]).ravel()[indices], geometry["latitude"], atol=1e-5, rtol=0)
            np.testing.assert_allclose(np.asarray(grid["lon"][:]).ravel()[indices], geometry["longitude"], atol=1e-5, rtol=0)
            dst.createDimension("grid_size", len(indices))
            dst.createDimension("grid_rank", 1)
            dst.createDimension("grid_corners", 4)
            dst.createVariable("grid_dims", "i4", ("grid_rank",))[:] = [len(indices)]
            dst.createVariable("grid_imask", "i4", ("grid_size",))[:] = 1
            for name, key in (("lat", "latitude"), ("lon", "longitude")):
                center = dst.createVariable("grid_center_" + name, "f8", ("grid_size",))
                center.units = "radians"
                center[:] = np.deg2rad(geometry[key])
                corners = np.asarray(grid[name + "_bnds"][:]).reshape(-1, 4)[indices]
                if not np.isfinite(corners).all():
                    raise ValueError("Target cell corners are incomplete")
                var = dst.createVariable("grid_corner_" + name, "f8", ("grid_size", "grid_corners"))
                var.units = "radians"
                var[:] = np.deg2rad(corners)
        with Dataset(source, "w") as dst:
            for name, values in (("lat", source_lat), ("lon", source_lon)):
                dst.createDimension(name, len(values))
                var = dst.createVariable(name, "f8", (name,))
                var.units = "degrees_north" if name == "lat" else "degrees_east"
                var.standard_name = "latitude" if name == "lat" else "longitude"
                var[:] = values
            dst.createVariable("precipitation", "f4", ("lat", "lon"))[:] = 1
        partial = destination.with_suffix(".part.nc")
        command = ["cdo", "-O", f"gencon,{target}", str(source), str(partial)]
        result = subprocess.run(command, env={**os.environ, "CDO_REMAP_NORM": "destarea", "REMAP_EXTRAPOLATE": "off"},
                                check=True, text=True, capture_output=True)
        with Dataset(partial, "r+") as dst:
            dst.gfs_geometry_indices_sha256 = sha(indices)
            dst.gfs_source_lat_sha256 = sha(source_lat)
            dst.gfs_source_lon_sha256 = sha(source_lon)
            dst.gfs_envelope_sha256 = str(geometry["envelope_sha256"])
            dst.gfs_target_lat_sha256 = str(geometry["target_lat_sha256"])
            dst.gfs_target_lon_sha256 = str(geometry["target_lon_sha256"])
        operator = ConservativeGap(partial, geometry)
        with Dataset(partial) as weights:
            area = np.asarray(weights["dst_grid_area"][:])
            source_area = np.asarray(weights["src_grid_area"][:])
            fraction = np.asarray(weights["src_grid_frac"][:])
        random_depth = np.random.default_rng(19).uniform(0, 10, operator.matrix.shape[1])
        mapped_volume = np.sum(operator.matrix @ random_depth * area)
        source_overlap_volume = np.sum(random_depth * source_area * fraction)
        relative_error = abs(mapped_volume-source_overlap_volume) / source_overlap_volume
        if relative_error > 1e-6:
            raise ValueError(f"Conservative area/volume test failed: {relative_error}")
        report = {"method": "CDO gencon, destarea; no extrapolation", "cells": len(indices),
                  "links": operator.matrix.nnz, "constant_field_max_error": operator.constant_error,
                  "random_field_overlap_volume_relative_error": float(relative_error),
                  "command": command, "diagnostic": result.stdout + result.stderr,
                  "note": "Conservation is over the target gap footprint, not the entire GFS source rectangle"}
        os.replace(partial, destination)
        destination.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
        return report


class ConservativeGap:
    def __init__(self, path: Path, geometry):
        with Dataset(path) as data:
            expected = {"gfs_geometry_indices_sha256": sha(geometry["indices"]),
                        "gfs_source_lat_sha256": sha(geometry["source_lat"]),
                        "gfs_source_lon_sha256": sha(geometry["source_lon"]),
                        "gfs_envelope_sha256": str(geometry["envelope_sha256"]),
                        "gfs_target_lat_sha256": str(geometry["target_lat_sha256"]),
                        "gfs_target_lon_sha256": str(geometry["target_lon_sha256"])}
            if any(getattr(data, k, None) != v for k, v in expected.items()):
                raise ValueError("Conservative weights do not match gap/source/target identity")
            rows = np.asarray(data["dst_address"][:], dtype=np.int64) - 1
            columns = np.asarray(data["src_address"][:], dtype=np.int64) - 1
            coefficients = np.asarray(data["remap_matrix"][:, 0])
            if np.any(coefficients < -1e-12) or not np.isfinite(coefficients).all():
                raise ValueError("Invalid conservative coefficients")
            self.matrix = csr_matrix((coefficients, (rows, columns)), shape=(len(geometry["indices"]),
                len(geometry["source_lat"]) * len(geometry["source_lon"])))
        self.constant_error = float(np.max(np.abs(np.asarray(self.matrix.sum(axis=1)).ravel() - 1)))
        if self.constant_error > 1e-6:
            raise ValueError(f"Incomplete conservative target coverage: {self.constant_error}")

    def __call__(self, depth):
        values = np.asarray(depth).ravel()
        if values.size != self.matrix.shape[1] or not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError("Invalid precipitation source for conservative remapping")
        return np.asarray(self.matrix @ values)
