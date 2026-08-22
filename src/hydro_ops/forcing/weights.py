"""Generate versioned CDO remapping weights with reproducibility manifests."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.forcing.inventory import inspect_forcing_file, netcdf_grid_fingerprint

OPERATORS = {"bilinear": "genbil", "conservative": "gencon", "nearest": "gennn"}


@lru_cache(maxsize=8)
def _target_grid_fingerprint(path: Path) -> str:
    return netcdf_grid_fingerprint(
        path, ("lat", "lon", "lat_bnds", "lon_bnds", "active_domain")
    )


def build_weight_command(
    executable: str,
    source: Path,
    target_grid: Path,
    output: Path,
    *,
    method: str,
    variable: str,
) -> list[str]:
    if method not in OPERATORS:
        raise ValueError(f"Unknown remapping method {method!r}")
    return [
        executable,
        "-O",
        f"{OPERATORS[method]},{target_grid}",
        f"-selname,{variable}",
        str(source),
        str(output),
    ]


def _valid_weights(path: Path) -> bool:
    try:
        with Dataset(path) as data:
            return all(name in data.variables for name in ("src_address", "dst_address", "remap_matrix"))
    except OSError:
        return False


def generate_weights(
    source: Path,
    product: str,
    variable: str,
    target_grid: Path,
    output: Path,
    *,
    method: str = "bilinear",
    cdo: str = "cdo",
    cdo_source: Path | None = None,
    cdo_variable: str | None = None,
    force: bool = False,
) -> tuple[Path, Path]:
    """Generate one weight file and a manifest tied to source/target grid identities."""
    inventory = inspect_forcing_file(source, product)
    if not inventory.valid:
        raise ValueError(f"Invalid source file: {'; '.join(inventory.issues)}")
    if variable not in inventory.variables:
        raise ValueError(f"Variable {variable!r} is absent from {source}")
    executable = shutil.which(cdo)
    if not executable:
        raise RuntimeError(f"CDO executable not found: {cdo}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(f"{output.suffix}.manifest.json")
    if output.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output}")
    partial = output.with_name(f"{output.stem}.part{output.suffix}")
    partial.unlink(missing_ok=True)
    geometry_source = cdo_source or source
    geometry_variable = cdo_variable or variable
    command = build_weight_command(
        executable,
        geometry_source,
        target_grid,
        partial,
        method=method,
        variable=geometry_variable,
    )
    try:
        # HDF5 2.1 emits a diagnostic while probing a nonexistent NetCDF output. A valid empty
        # placeholder avoids that noise; CDO's -O replaces it immediately.
        with Dataset(partial, "w"):
            pass
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "no CDO diagnostic").strip()
            raise RuntimeError(f"CDO weight generation failed: {details}") from error
        if not _valid_weights(partial):
            raise RuntimeError(f"CDO did not create valid remapping weights: {partial}")
        partial.replace(output)
        version_result = subprocess.run(
            [executable, "--version"], check=True, capture_output=True, text=True
        )
        version_lines = (version_result.stdout + version_result.stderr).splitlines()
        version = version_lines[0] if version_lines else "unknown"
        metadata = {
            "created": datetime.now(UTC).isoformat(),
            "method": method,
            "source_product": product,
            "source_example": str(source),
            "source_grid_fingerprint": inventory.grid_fingerprint,
            "geometry_source": str(geometry_source),
            "target_grid": str(target_grid),
            "target_grid_fingerprint": netcdf_grid_fingerprint(
                target_grid, ("lat", "lon", "lat_bnds", "lon_bnds", "active_domain")
            ),
            "variable": variable,
            "geometry_variable": geometry_variable,
            "cdo_version": version,
            "command": command,
            "cdo_stderr": completed.stderr,
        }
        manifest_partial = manifest.with_name(f"{manifest.name}.part")
        manifest_partial.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        manifest_partial.replace(manifest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return output, manifest


def validate_weight_manifest(
    source: Path,
    product: str,
    target_grid: Path,
    weights: Path,
    *,
    expected_method: str = "bilinear",
) -> dict:
    """Reject a weight matrix whose manifest does not match either current grid."""
    manifest = weights.with_suffix(f"{weights.suffix}.manifest.json")
    if not manifest.is_file():
        raise FileNotFoundError(f"Weight manifest is missing: {manifest}")
    try:
        metadata = json.loads(manifest.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid weight manifest JSON: {manifest}") from error
    inventory = inspect_forcing_file(source, product)
    if not inventory.valid:
        raise ValueError(f"Invalid {product} source: {'; '.join(inventory.issues)}")
    target_fingerprint = _target_grid_fingerprint(target_grid.resolve())
    expected = {
        "source_grid_fingerprint": inventory.grid_fingerprint,
        "target_grid_fingerprint": target_fingerprint,
        "method": expected_method,
    }
    mismatches = [
        f"{name}: manifest={metadata.get(name)!r}, current={value!r}"
        for name, value in expected.items()
        if metadata.get(name) != value
    ]
    if mismatches:
        raise ValueError(f"Stale or incompatible weights {weights}: {'; '.join(mismatches)}")
    if not _valid_weights(weights):
        raise ValueError(f"Invalid weight matrix: {weights}")
    return metadata
