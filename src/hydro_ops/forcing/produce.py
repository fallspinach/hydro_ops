"""Orchestrate selection, processing, and seven-field hourly assembly."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from hydro_ops.forcing.assemble import assemble_seven_field_hour
from hydro_ops.forcing.hybrid import HybridWeights, write_hybrid_components
from hydro_ops.forcing.radiation_wind_hour import process_radiation_wind_hour
from hydro_ops.forcing.source_selection import (
    SelectedSource,
    select_hourly_source,
    source_paths,
)
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
    hybrid_weights: HybridWeights | None = None,
    hybrid_window_cells: int = 33,
    hrrr_relative_humidity_tolerance: float = 0.20,
    work_directory: Path | None = None,
    force: bool = False,
) -> tuple[Path, SelectedSource]:
    """Produce one complete non-precipitation forcing hour from one selected product."""
    if output.exists() and not force:
        raise FileExistsError(f"Output exists; use --force to replace it: {output}")
    hybrid_weights = HybridWeights() if hybrid_weights is None else hybrid_weights
    hybrid_weights.validate()
    if hybrid_weights.enabled() and final_temperature is not None:
        raise ValueError(
            "Apply the daily PRISM temperature constraint after producing all 24 hybrid hours"
        )
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
            valid_time=selected.valid_time, final_temperature_path=final_temperature,
            relative_humidity_tolerance=(
                hrrr_relative_humidity_tolerance if selected.product == "hrrr" else 0.10
            ),
            reject_material_rh_excursions=False,
            work_directory=temporary,
        )
        process_radiation_wind_hour(
            selected.path, selected.product, target_grid, weights, radiation_wind,
            valid_time=selected.valid_time, work_directory=temporary,
        )
        assembled_source = selected
        if selected.product == "nldas2" and hybrid_weights.enabled():
            hrrr_path = next(
                (
                    path for path in source_paths("hrrr", hrrr_root, valid_time)
                    if path.is_file()
                ),
                None,
            )
            if hrrr_path is None:
                raise FileNotFoundError(
                    f"HRRR is required by enabled hybrid weights for {valid_time.isoformat()}"
                )
            hrrr_thermo = temporary / "hrrr_thermodynamic.nc"
            hrrr_radiation = temporary / "hrrr_radiation_wind.nc"
            process_thermodynamic_hour(
                hrrr_path, "hrrr", hrrr_elevation, "HGT_surface",
                target_grid, target_elevation, hrrr_weights, hrrr_thermo,
                valid_time=valid_time, final_temperature_path=final_temperature,
                relative_humidity_tolerance=hrrr_relative_humidity_tolerance,
                reject_material_rh_excursions=False,
                work_directory=temporary,
            )
            process_radiation_wind_hour(
                hrrr_path, "hrrr", target_grid, hrrr_weights, hrrr_radiation,
                valid_time=valid_time, work_directory=temporary,
            )
            hybrid_thermo = temporary / "hybrid_thermodynamic.nc"
            hybrid_radiation = temporary / "hybrid_radiation_wind.nc"
            write_hybrid_components(
                thermo, hrrr_thermo, radiation_wind, hrrr_radiation,
                hybrid_thermo, hybrid_radiation, hybrid_weights,
                window=hybrid_window_cells,
            )
            thermo, radiation_wind = hybrid_thermo, hybrid_radiation
            assembled_source = SelectedSource(
                "nldas2_hrrr_hybrid", selected.path, selected.valid_time,
                selected.fallback_used, selected.rejected,
            )
        assemble_seven_field_hour(
            thermo, radiation_wind, target_grid, output,
            fallback_used=selected.fallback_used, force=force,
        )
    return output, assembled_source
