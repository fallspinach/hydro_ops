"""Operational discovery and atomic production of complete hourly forcing."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.assemble import add_precipitation_to_ldasin
from hydro_ops.forcing.hybrid import HybridWeights
from hydro_ops.forcing.precipitation_hour import process_precipitation_hour
from hydro_ops.forcing.produce import produce_seven_field_hour
from hydro_ops.forcing.source_selection import source_path, source_paths

REQUIRED_FINAL_VARIABLES = {
    "T2D", "Q2D", "PSFC", "U2D", "V2D", "SWDOWN", "LWDOWN", "RAINRATE"
}


@dataclass(frozen=True)
class OperationalLayout:
    nldas2_root: Path
    hrrr_root: Path
    mrms_root: Path
    stage4_root: Path
    target_grid: Path
    remap_grid: Path
    target_elevation: Path
    nldas2_elevation: Path
    hrrr_elevation: Path
    nldas2_bilinear: Path
    hrrr_bilinear: Path
    nldas2_conservative: Path
    hrrr_conservative: Path
    mrms_conservative: Path
    mrms_quality_bilinear: Path
    stage4_conservative: Path

    @classmethod
    def project_defaults(cls, root: Path = Path(".")) -> OperationalLayout:
        data = root / "data"
        remap = data / "static/remapping/nwm_conus_1km"
        return cls(
            data / "forcing/nasa/nldas2/fora0125_hourly_v2.0",
            data / "forcing/noaa/hrrr/conus/3km/hourly",
            data / "forcing/noaa/mrms/conus/1km/hourly/netcdf",
            data / "forcing/noaa/stage4/netcdf",
            data / "static/nwm/forcing_grid/nwm_conus_1km_grid.nc",
            data / "static/nwm/forcing_grid/nwm_conus_1km_scrip.nc",
            data / "static/nwm/forcing_grid/nwm_conus_1km_elevation.nc",
            data / "static/nldas2/NLDAS_elevation.nc4",
            data / "static/hrrr/conus/hrrr_static.2022100100.grib2.nc",
            remap / "nldas2_bilinear.nc",
            remap / "hrrr_bilinear.nc",
            remap / "nldas2_conservative.nc",
            remap / "hrrr_conservative.nc",
            remap / "mrms_conservative.nc",
            remap / "mrms_quality_bilinear.nc",
            remap / "stage4_conservative.nc",
        )


def discover_precipitation_candidates(
    valid_time: datetime, layout: OperationalLayout
) -> tuple[dict[str, Path], Path | None]:
    """Discover exact-time candidates without treating absent revisions as errors."""
    valid_time = valid_time.astimezone(UTC)
    stamp = valid_time.strftime("%Y%m%d-%H0000")
    directory = valid_time.strftime("%Y/%m/%d")
    candidates = {
        "mrms_pass2": layout.mrms_root / "pass2" / directory
        / f"MRMS_MultiSensor_QPE_01H_Pass2_00.00_{stamp}.grib2.nc",
        "mrms_pass1": layout.mrms_root / "pass1" / directory
        / f"MRMS_MultiSensor_QPE_01H_Pass1_00.00_{stamp}.grib2.nc",
        "stage4_archive": layout.stage4_root / "archive" / directory
        / f"st4_conus.{valid_time:%Y%m%d%H}.01h.grb2.nc",
        "stage4_realtime": layout.stage4_root / "realtime" / directory
        / f"st4_conus.{valid_time:%Y%m%d%H}.01h.grb2.nc",
        "nldas2": next(
            (
                path for path in source_paths("nldas2", layout.nldas2_root, valid_time)
                if path.is_file()
            ),
            source_path("nldas2", layout.nldas2_root, valid_time),
        ),
        "hrrr": next(
            (
                path for path in source_paths("hrrr", layout.hrrr_root, valid_time)
                if path.is_file()
            ),
            source_path("hrrr", layout.hrrr_root, valid_time),
        ),
    }
    quality = layout.mrms_root / "quality" / directory / (
        f"MRMS_RadarAccumulationQualityIndex_01H_00.00_{stamp}.grib2.nc"
    )
    return {name: path for name, path in candidates.items() if path.is_file()}, (
        quality if quality.is_file() else None
    )


def valid_complete_hour(path: Path, valid_time: datetime) -> bool:
    try:
        with Dataset(path) as dataset:
            return (
                REQUIRED_FINAL_VARIABLES <= set(dataset.variables)
                and dataset.getncattr("valid_time")
                == valid_time.astimezone(UTC).replace(tzinfo=None).isoformat()
                and dataset.getncattr("precipitation_status") == "present"
            )
    except (OSError, AttributeError):
        return False


def produce_complete_hour(
    valid_time: datetime,
    layout: OperationalLayout,
    output: Path,
    *,
    work_directory: Path,
    final_temperature: Path | None = None,
    mrms_quality_threshold: float = 0.5,
    hybrid_weights: HybridWeights | None = None,
    hybrid_window_cells: int = 33,
    force: bool = False,
) -> dict:
    """Produce or resume one complete, provenance-rich eight-field LDASIN hour."""
    valid_time = valid_time.astimezone(UTC)
    if not force and valid_complete_hour(output, valid_time):
        return {"valid_time": valid_time.isoformat(), "status": "skipped", "output": str(output)}
    candidates, quality = discover_precipitation_candidates(valid_time, layout)
    if not candidates:
        raise FileNotFoundError(f"No precipitation candidates for {valid_time.isoformat()}")
    weights = {
        name: (
            layout.mrms_conservative
            if name.startswith("mrms_")
            else layout.stage4_conservative
            if name.startswith("stage4_")
            else layout.nldas2_conservative
            if name == "nldas2"
            else layout.hrrr_conservative
        )
        for name in candidates
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    work_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hydro_ops_complete_hour_", dir=work_directory) as temp:
        temp = Path(temp)
        seven = temp / "seven.LDASIN_DOMAIN1"
        precipitation = temp / "precipitation.nc"
        _, selected = produce_seven_field_hour(
            valid_time, layout.nldas2_root, layout.hrrr_root, layout.target_grid,
            layout.target_elevation, layout.nldas2_elevation, layout.hrrr_elevation,
            layout.nldas2_bilinear, layout.hrrr_bilinear, seven,
            final_temperature=final_temperature, hybrid_weights=hybrid_weights,
            hybrid_window_cells=hybrid_window_cells, work_directory=temp,
        )
        process_precipitation_hour(
            candidates, weights, layout.target_grid, precipitation,
            valid_time=valid_time, remap_grid_path=layout.remap_grid, quality_path=quality,
            quality_weights=layout.mrms_quality_bilinear if quality else None,
            mrms_quality_threshold=mrms_quality_threshold, work_directory=temp,
        )
        add_precipitation_to_ldasin(seven, precipitation, output, force=True)
    with Dataset(output) as dataset:
        source_ids, source_counts = np.unique(
            dataset["precip_source_id"][:], return_counts=True
        )
    summary = {
        "valid_time": valid_time.isoformat(),
        "status": "produced",
        "output": str(output),
        "forcing_source": selected.product,
        "forcing_fallback": selected.fallback_used,
        "hybrid_weights": None if hybrid_weights is None else hybrid_weights.__dict__,
        "hybrid_window_cells": hybrid_window_cells,
        "precipitation_candidates": sorted(candidates),
        "precipitation_source_counts": {
            str(int(source)): int(count)
            for source, count in zip(source_ids, source_counts, strict=True)
        },
    }
    manifest = output.with_suffix(f"{output.suffix}.manifest.json")
    partial = manifest.with_name(f"{manifest.name}.part")
    partial.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    partial.replace(manifest)
    return summary
