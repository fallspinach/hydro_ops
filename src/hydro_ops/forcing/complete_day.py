"""Daily-batched production of complete hourly NWM forcing files."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

from hydro_ops.forcing.assemble import add_precipitation_to_ldasin, assemble_seven_field_hour
from hydro_ops.forcing.operations import (
    OperationalLayout,
    discover_precipitation_candidates,
    valid_complete_hour,
)
from hydro_ops.forcing.physics import lambert_grid_x_angle, rotate_grid_to_earth
from hydro_ops.forcing.precipitation_day import process_precipitation_day
from hydro_ops.forcing.radiation_wind_hour import _write_output as write_radiation_output
from hydro_ops.forcing.source_selection import SelectedSource, select_hourly_source
from hydro_ops.forcing.static_validation import StaticValidation, validate_terrain_bundle
from hydro_ops.forcing.thermodynamic_hour import (
    QC_FRACTION_VARIABLES,
    REFERENCE_VARIABLES,
    build_remap_command,
)
from hydro_ops.forcing.thermodynamic_hour import (
    _create_output as create_thermodynamic_output,
)
from hydro_ops.forcing.thermodynamics import ThermodynamicQC, prepare_reference_state
from hydro_ops.forcing.weights import validate_weight_manifest


def utc_hours(day: date) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [start + timedelta(hours=hour) for hour in range(24)]


@dataclass(frozen=True)
class _AssemblyTask:
    index: int
    selected: SelectedSource
    remapped: Path
    precipitation: Path
    staged_output: Path
    published_output: Path
    temporary: Path
    target_grid: Path
    target_elevation: Path
    weights: Path
    static_validation: StaticValidation
    products: tuple[str, ...]


def _assemble_hour(task: _AssemblyTask) -> dict:
    """Assemble one independent hour after all daily remaps have completed."""
    from hydro_ops.forcing.normalize import open_normalized_forcing

    started = time.perf_counter()
    product = task.selected.product
    with open_normalized_forcing(
        task.selected.path, product, valid_time=task.selected.valid_time
    ) as hourly_source:
        thermo = task.temporary / f"thermo.{task.index:02d}.nc"
        radiation = task.temporary / f"radiation.{task.index:02d}.nc"
        seven = task.temporary / f"seven.{task.index:02d}.nc"
        create_thermodynamic_output(
            thermo,
            task.remapped,
            task.target_grid,
            task.target_elevation,
            source=hourly_source,
            weights_path=task.weights,
            final_temperature=None,
            relative_humidity_tolerance=0.20 if product == "hrrr" else 0.10,
            static_validation=task.static_validation,
            remapped_time_index=task.index,
        )
        write_radiation_output(
            radiation,
            task.remapped,
            task.target_grid,
            source=hourly_source,
            weights_path=task.weights,
            solar_elevation_tolerance_degrees=-0.833,
            maximum_shortwave=1400.0,
            remapped_time_index=task.index,
        )
    assemble_seven_field_hour(
        thermo,
        radiation,
        task.target_grid,
        seven,
        fallback_used=task.selected.fallback_used,
        force=True,
    )
    add_precipitation_to_ldasin(seven, task.precipitation, task.staged_output, force=True)
    summary = {
        "valid_time": task.selected.valid_time.isoformat(),
        "status": "produced",
        "output": str(task.published_output),
        "forcing_source": product,
        "forcing_fallback": task.selected.fallback_used,
        "precipitation_candidates": list(task.products),
        "remap_mode": "daily_batch",
        "assembly_seconds": round(time.perf_counter() - started, 3),
    }
    manifest = task.staged_output.with_suffix(f"{task.staged_output.suffix}.manifest.json")
    manifest.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def _load_source_day(
    selections: list[SelectedSource], source_elevation_path: Path, elevation_variable: str
) -> tuple[xr.Dataset, np.ndarray]:
    from hydro_ops.forcing.normalize import open_normalized_forcing

    fields: list[xr.Dataset] = []
    try:
        for selected in selections:
            source = open_normalized_forcing(
                selected.path, selected.product, valid_time=selected.valid_time
            )
            fields.append(source.load())
            source.close()
        combined = xr.concat(fields, dim="time")
    finally:
        for field in fields:
            field.close()
    with xr.open_dataset(source_elevation_path, mask_and_scale=True) as terrain:
        elevation = np.asarray(terrain[elevation_variable].squeeze().values, dtype=np.float64)
    return combined, elevation


def _write_native_batch(
    source: xr.Dataset,
    source_elevation: np.ndarray,
    product: str,
    destination: Path,
) -> None:
    temperature = np.asarray(source["air_temperature"].values, dtype=np.float64)
    pressure = np.asarray(source["surface_pressure"].values, dtype=np.float64)
    humidity = np.asarray(source["specific_humidity"].values, dtype=np.float64)
    longwave = np.asarray(source["downward_longwave"].values, dtype=np.float64)
    if temperature.ndim != 3 or source_elevation.shape != temperature.shape[-2:]:
        raise ValueError("Daily source or source elevation has the wrong shape")
    state = prepare_reference_state(
        temperature,
        pressure,
        humidity,
        longwave,
        source_elevation,
        relative_humidity_tolerance=0.20 if product == "hrrr" else 0.10,
        reject_material_rh_excursions=False,
    )
    shortwave = np.asarray(source["downward_shortwave"].values, dtype=np.float64)
    finite_shortwave = np.isfinite(shortwave)
    if np.any(shortwave[finite_shortwave] < -0.1):
        raise ValueError("Source shortwave radiation is materially negative")
    shortwave = np.where(finite_shortwave, np.maximum(shortwave, 0.0), np.nan)
    eastward = np.asarray(source["wind_u"].values, dtype=np.float64)
    northward = np.asarray(source["wind_v"].values, dtype=np.float64)
    if product == "hrrr":
        angle = lambert_grid_x_angle(
            source["longitude"].values,
            central_longitude=-97.5,
            standard_parallel_1=38.5,
            standard_parallel_2=38.5,
        )
        eastward, northward = rotate_grid_to_earth(eastward, northward, angle)
    invalid_vector = ~(np.isfinite(eastward) & np.isfinite(northward))
    eastward = np.where(invalid_vector, np.nan, eastward)
    northward = np.where(invalid_vector, np.nan, northward)
    dims = source["air_temperature"].dims
    coords = {name: source.coords[name] for name in dims if name in source.coords}
    for name in ("latitude", "longitude"):
        if name in source.coords:
            coords[name] = source.coords[name]
    variables = {
        name: (dims, values.astype(np.float32))
        for name, values in zip(
            REFERENCE_VARIABLES,
            (state.temperature, state.pressure, state.relative_humidity, state.longwave_factor),
            strict=True,
        )
    }
    variables.update(
        {
            QC_FRACTION_VARIABLES[0]: (
                dims,
                ((state.qc_flags & ThermodynamicQC.RH_CLIPPED_LOW) != 0).astype(np.float32),
            ),
            QC_FRACTION_VARIABLES[1]: (
                dims,
                ((state.qc_flags & ThermodynamicQC.RH_CLIPPED_HIGH) != 0).astype(np.float32),
            ),
            "shortwave": (dims, shortwave.astype(np.float32)),
            "eastward_wind": (dims, eastward.astype(np.float32)),
            "northward_wind": (dims, northward.astype(np.float32)),
            "source_shortwave_negative": (
                dims,
                (finite_shortwave & (np.asarray(source["downward_shortwave"]) < 0)).astype(
                    np.float32
                ),
            ),
        }
    )
    output = xr.Dataset(variables, coords=coords)
    output.attrs.update(
        {
            "source_product": product,
            "batch_hours": 24,
            "batch_start": str(source.time.values[0]),
            "batch_end": str(source.time.values[-1]),
        }
    )
    encoding = {
        name: {"zlib": True, "complevel": 1, "shuffle": True}
        for name in output.data_vars
    }
    output.to_netcdf(destination, encoding=encoding)


def produce_complete_day(
    day: date,
    layout: OperationalLayout,
    output_root: Path,
    *,
    work_directory: Path,
    mrms_quality_threshold: float = 0.5,
    assembly_workers: int = 4,
    precipitation_remap_workers: int = 1,
    force: bool = False,
) -> list[dict]:
    """Remap all variables in daily batches and publish 24 hourly LDASIN files."""
    if assembly_workers <= 0:
        raise ValueError("assembly_workers must be positive")
    if precipitation_remap_workers <= 0:
        raise ValueError("precipitation_remap_workers must be positive")
    valid_times = utc_hours(day)
    outputs = [
        output_root / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        for valid in valid_times
    ]
    if not force and all(valid_complete_hour(path, valid) for path, valid in zip(outputs, valid_times, strict=True)):
        return [
            {"valid_time": valid.isoformat(), "status": "skipped", "output": str(path)}
            for path, valid in zip(outputs, valid_times, strict=True)
        ]
    selections = [
        select_hourly_source(valid, layout.nldas2_root, layout.hrrr_root)
        for valid in valid_times
    ]
    product = selections[0].product
    if any(selected.product != product for selected in selections):
        raise ValueError("Daily batching requires one non-precipitation source for all 24 hours")
    static = {
        "nldas2": (layout.nldas2_elevation, "NLDAS_elev", layout.nldas2_bilinear),
        "hrrr": (layout.hrrr_elevation, "HGT_surface", layout.hrrr_bilinear),
    }
    source_elevation_path, elevation_variable, bilinear_weights = static[product]
    candidates_and_quality = [
        discover_precipitation_candidates(valid, layout) for valid in valid_times
    ]
    candidate_hours = [item[0] for item in candidates_and_quality]
    quality_hours = [item[1] for item in candidates_and_quality]
    products = set().union(*(set(item) for item in candidate_hours))
    precipitation_weights = {
        name: (
            layout.mrms_conservative
            if name.startswith("mrms_")
            else layout.stage4_conservative
            if name.startswith("stage4_")
            else layout.nldas2_conservative
            if name == "nldas2"
            else layout.hrrr_conservative
        )
        for name in products
    }
    executable = shutil.which("cdo")
    if not executable:
        raise RuntimeError("CDO executable not found")
    work_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"complete_day_{day:%Y%m%d}_", dir=work_directory) as temporary:
        temporary = Path(temporary)
        tasks: list[_AssemblyTask] = []
        source, source_elevation = _load_source_day(
            selections, source_elevation_path, elevation_variable
        )
        try:
            validate_weight_manifest(
                selections[0].path, product, layout.target_grid, bilinear_weights
            )
            static_validation = validate_terrain_bundle(
                source_elevation_path,
                elevation_variable,
                source_elevation.shape,
                layout.target_grid,
                layout.target_elevation,
            )
            native = temporary / "nonprecip.native.nc"
            remapped = temporary / "nonprecip.remapped.nc"
            stage_started = time.perf_counter()
            _write_native_batch(source, source_elevation, product, native)
            command = build_remap_command(
                executable, layout.remap_grid, bilinear_weights, native, remapped
            )
            subprocess.run(command, check=True, capture_output=True, text=True)
            print(
                json.dumps(
                    {
                        "day": day.isoformat(),
                        "stage": "nonprecipitation_remap",
                        "seconds": round(time.perf_counter() - stage_started, 3),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            stage_started = time.perf_counter()
            precipitation = process_precipitation_day(
                valid_times,
                candidate_hours,
                quality_hours,
                precipitation_weights,
                layout.target_grid,
                layout.remap_grid,
                temporary / "precipitation",
                quality_weights=(
                    layout.mrms_quality_bilinear if any(quality_hours) else None
                ),
                mrms_quality_threshold=mrms_quality_threshold,
                remap_workers=precipitation_remap_workers,
                work_directory=temporary,
                force=True,
            )
            print(
                json.dumps(
                    {
                        "day": day.isoformat(),
                        "stage": "precipitation_remap_and_composite",
                        "seconds": round(time.perf_counter() - stage_started, 3),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            tasks = [
                _AssemblyTask(
                    index=index,
                    selected=selected,
                    remapped=remapped,
                    precipitation=precipitation[index],
                    staged_output=final_output.with_name(
                        f".{final_output.name}.daypart-{temporary.name}"
                    ),
                    published_output=final_output,
                    temporary=temporary,
                    target_grid=layout.target_grid,
                    target_elevation=layout.target_elevation,
                    weights=bilinear_weights,
                    static_validation=static_validation,
                    products=tuple(sorted(candidate_hours[index])),
                )
                for index, (selected, final_output) in enumerate(
                    zip(selections, outputs, strict=True)
                )
            ]
            for task in tasks:
                task.staged_output.parent.mkdir(parents=True, exist_ok=True)
            stage_started = time.perf_counter()
            with ProcessPoolExecutor(max_workers=min(assembly_workers, 24)) as executor:
                summaries = list(executor.map(_assemble_hour, tasks))
            for task in tasks:
                task.published_output.parent.mkdir(parents=True, exist_ok=True)
                staged_manifest = task.staged_output.with_suffix(
                    f"{task.staged_output.suffix}.manifest.json"
                )
                published_manifest = task.published_output.with_suffix(
                    f"{task.published_output.suffix}.manifest.json"
                )
                task.staged_output.replace(task.published_output)
                staged_manifest.replace(published_manifest)
            print(
                json.dumps(
                    {
                        "day": day.isoformat(),
                        "stage": "hourly_assembly",
                        "workers": assembly_workers,
                        "seconds": round(time.perf_counter() - stage_started, 3),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return summaries
        finally:
            source.close()
            for task in tasks:
                task.staged_output.unlink(missing_ok=True)
                task.staged_output.with_suffix(
                    f"{task.staged_output.suffix}.manifest.json"
                ).unlink(missing_ok=True)
