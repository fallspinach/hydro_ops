"""Orchestrate selection, processing, and seven-field hourly assembly."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from hydro_ops.forcing.assemble import assemble_seven_field_hour
from hydro_ops.forcing.radiation_wind_hour import process_radiation_wind_hour
from hydro_ops.forcing.source_selection import SelectedSource, select_hourly_source
from hydro_ops.forcing.thermodynamic_hour import process_thermodynamic_hour


def produce_seven_field_hour(
    valid_time: datetime,
    nldas2_root: Path,
    hrrr_root: Path,
    target_grid: Path,
    target_elevation: Path,
    nldas2_elevation: Path,
    hrrr_elevation: Path,
    nldas2_weights: Path,
    hrrr_weights: Path,
    output: Path,
    *,
    final_temperature: Path | None = None,
    work_directory: Path | None = None,
    force: bool = False,
) -> tuple[Path, SelectedSource]:
    """Produce one complete non-precipitation forcing hour from one selected product."""
    if output.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output}")
    selected = select_hourly_source(valid_time, nldas2_root, hrrr_root)
    static = {
        "nldas2": (nldas2_elevation, "NLDAS_elev", nldas2_weights),
        "hrrr": (hrrr_elevation, "HGT_surface", hrrr_weights),
    }
    source_elevation, elevation_variable, weights = static[selected.product]
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = output.parent if work_directory is None else work_directory
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hydro_ops_produce_hour_", dir=scratch) as temporary:
        temporary = Path(temporary)
        thermo = temporary / "thermodynamic.nc"
        radiation_wind = temporary / "radiation_wind.nc"
        process_thermodynamic_hour(
            selected.path, selected.product, source_elevation, elevation_variable,
            target_grid, target_elevation, weights, thermo,
            final_temperature_path=final_temperature, work_directory=temporary,
        )
        process_radiation_wind_hour(
            selected.path, selected.product, target_grid, weights, radiation_wind,
            work_directory=temporary,
        )
        assemble_seven_field_hour(
            thermo, radiation_wind, target_grid, output,
            fallback_used=selected.fallback_used, force=force,
        )
    return output, selected
