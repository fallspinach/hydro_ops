"""Normalize and quality-select hourly precipitation candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntFlag
from pathlib import Path

import numpy as np
import xarray as xr
from numpy.typing import NDArray

MRMS_VARIABLES = {
    "mrms_pass1": "MultiSensorQPE01HPass1_0mabovemeansealevel",
    "mrms_pass2": "MultiSensorQPE01HPass2_0mabovemeansealevel",
    "mrms_quality": "RadarAccumulationQualityIndex01H_0mabovemeansealevel",
}
PRECIPITATION_VARIABLES = {
    **{name: variable for name, variable in MRMS_VARIABLES.items() if name != "mrms_quality"},
    "stage4_archive": "APCP_surface",
    "stage4_realtime": "APCP_surface",
    "nldas2": "Rainf",
    "hrrr": "APCP_surface",
}
SOURCE_IDS = {
    "mrms_pass2": 1,
    "mrms_pass1": 2,
    "stage4_archive": 3,
    "stage4_realtime": 4,
    "nldas2": 5,
    "hrrr": 6,
}


class PrecipitationQC(IntFlag):
    """Per-cell precipitation QC flags."""

    MRMS_LOW_QUALITY = 1
    STAGE4_OVERRIDE = 2
    FALLBACK_USED = 4
    MISSING = 8
    NEGATIVE_REJECTED = 16
    EXTREME = 32


@dataclass(frozen=True)
class CompositePrecipitation:
    depth: NDArray[np.float64]
    source_id: NDArray[np.uint8]
    confidence: NDArray[np.float32]
    qc_flags: NDArray[np.uint16]


def open_precipitation_candidate(
    path: Path, product: str, *, valid_time: datetime | None = None
) -> xr.Dataset:
    """Open one native source as hour-ending accumulated depth or MRMS quality."""
    if product not in {*PRECIPITATION_VARIABLES, "mrms_quality"}:
        raise ValueError(f"Unknown precipitation product {product!r}")
    variable = (
        MRMS_VARIABLES[product]
        if product in MRMS_VARIABLES
        else PRECIPITATION_VARIABLES[product]
    )
    source = xr.open_dataset(path, mask_and_scale=True, decode_times=True)
    if variable not in source:
        source.close()
        raise ValueError(f"{path} is missing {variable}")
    field = source[variable]
    if valid_time is not None and field.sizes.get("time", 0) > 1:
        requested = np.datetime64(valid_time.replace(tzinfo=None), "ns")
        available = np.asarray(source.time.values).astype("datetime64[ns]")
        matches = np.flatnonzero(available == requested)
        if matches.size != 1:
            source.close()
            raise ValueError(f"{path} has no unique value for {valid_time.isoformat()}")
        field = field.isel(time=[int(matches[0])])
    if field.sizes.get("time") != 1:
        source.close()
        raise ValueError(f"{path} must contain exactly one time")
    if product == "mrms_quality":
        normalized = field.where((field >= 0) & (field <= 1)).rename("quality")
        normalized.attrs["units"] = "1"
    else:
        normalized = field.where(field >= 0).rename("precipitation_depth")
        normalized.attrs["units"] = "kg m-2"
    selected_valid_time = np.asarray(field.time.values).reshape(-1)[0].astype("datetime64[ns]")
    start_time = selected_valid_time - np.timedelta64(1, "h")
    dataset = normalized.to_dataset()
    dataset = dataset.assign_coords(
        time_bounds=(("time", "bounds"), np.array([[start_time, selected_valid_time]]))
    )
    dataset.attrs.update(
        {
            "source_product": product,
            "source_file": str(path),
            "accumulation_interval": "(T-1h,T]",
            "valid_time": np.datetime_as_string(selected_valid_time, unit="s"),
        }
    )
    return dataset


def composite_precipitation(
    candidates: dict[str, np.ndarray],
    *,
    mrms_quality: np.ndarray | None = None,
    mrms_quality_threshold: float = 0.5,
    stage4_override: np.ndarray | None = None,
    extreme_depth: float = 300.0,
) -> CompositePrecipitation:
    """Select one auditable source per cell without averaging precipitation features."""
    if not candidates:
        raise ValueError("At least one precipitation candidate is required")
    unknown = set(candidates) - SOURCE_IDS.keys()
    if unknown:
        raise ValueError(f"Unknown precipitation candidates: {sorted(unknown)}")
    shape = np.broadcast_shapes(*(np.asarray(value).shape for value in candidates.values()))
    depth = np.full(shape, np.nan, dtype=np.float64)
    source_id = np.zeros(shape, dtype=np.uint8)
    confidence = np.zeros(shape, dtype=np.float32)
    qc = np.zeros(shape, dtype=np.uint16)
    quality = (
        np.full(shape, np.nan)
        if mrms_quality is None
        else np.broadcast_to(np.asarray(mrms_quality, dtype=np.float64), shape)
    )
    override = (
        np.zeros(shape, dtype=bool)
        if stage4_override is None
        else np.broadcast_to(np.asarray(stage4_override, dtype=bool), shape)
    )
    arrays = {
        name: np.broadcast_to(np.asarray(values, dtype=np.float64), shape)
        for name, values in candidates.items()
    }

    def choose(product: str, eligible: np.ndarray, score: np.ndarray | float) -> None:
        if product not in arrays:
            return
        values = arrays[product]
        select = (source_id == 0) & eligible & np.isfinite(values) & (values >= 0)
        depth[select] = values[select]
        source_id[select] = SOURCE_IDS[product]
        confidence[select] = np.broadcast_to(score, shape)[select]

    stage_products = (
        "stage4_archive" if "stage4_archive" in arrays else "stage4_realtime"
    )
    if stage_products in arrays:
        choose(stage_products, override, 0.85 if stage_products == "stage4_archive" else 0.75)
        qc[(source_id == SOURCE_IDS[stage_products]) & override] |= np.uint16(
            PrecipitationQC.STAGE4_OVERRIDE
        )
    acceptable_mrms = np.isfinite(quality) & (quality >= mrms_quality_threshold)
    choose("mrms_pass2", acceptable_mrms, np.nan_to_num(quality, nan=0.0))
    choose("mrms_pass1", acceptable_mrms, np.nan_to_num(quality, nan=0.0) * 0.9)
    low_quality = np.isfinite(quality) & ~acceptable_mrms
    qc[low_quality] |= np.uint16(PrecipitationQC.MRMS_LOW_QUALITY)
    choose("stage4_archive", np.ones(shape, dtype=bool), 0.8)
    choose("stage4_realtime", np.ones(shape, dtype=bool), 0.7)
    choose("nldas2", np.ones(shape, dtype=bool), 0.4)
    choose("hrrr", np.ones(shape, dtype=bool), 0.2)
    fallback_ids = np.array([3, 4, 5, 6], dtype=np.uint8)
    qc[np.isin(source_id, fallback_ids)] |= np.uint16(PrecipitationQC.FALLBACK_USED)
    missing = source_id == 0
    qc[missing] |= np.uint16(PrecipitationQC.MISSING)
    qc[np.isfinite(depth) & (depth > extreme_depth)] |= np.uint16(PrecipitationQC.EXTREME)
    return CompositePrecipitation(depth, source_id, confidence, qc)
