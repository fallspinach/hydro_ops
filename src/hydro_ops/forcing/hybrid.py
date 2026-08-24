"""NLDAS-2 baseline plus HRRR target-grid mesoscale refinements."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from numpy.typing import ArrayLike, NDArray

from hydro_ops.forcing.physics import (
    cosgrove_atmospheric_emissivity,
    specific_humidity_from_relative_humidity,
)

HYBRID_REFINEMENT_APPLIED = np.uint16(1 << 14)
HYBRID_REFINEMENT_CLIPPED = np.uint16(1 << 13)


@dataclass(frozen=True)
class HybridWeights:
    """Dimensionless weights for independently validated HRRR anomaly terms."""

    temperature: float = 0.0
    log_pressure: float = 0.0
    relative_humidity: float = 0.0
    log_longwave_factor: float = 0.0
    clear_sky_index: float = 0.0
    wind_u: float = 0.0
    wind_v: float = 0.0

    def enabled(self) -> bool:
        return any(value != 0.0 for value in asdict(self).values())

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"Hybrid weight {name} must be finite and in [0, 1]")


@dataclass(frozen=True)
class HybridCaps:
    temperature_k: float = 8.0
    log_pressure: float = 0.08
    relative_humidity: float = 0.35
    log_longwave_factor: float = 0.50
    clear_sky_index: float = 0.75
    wind_m_s: float = 15.0


DEFAULT_HYBRID_CAPS = HybridCaps()


def _box_sum_axis(values: NDArray[np.float64], window: int, axis: int) -> NDArray[np.float64]:
    before = window // 2
    after = window - before - 1
    padding = [(0, 0)] * values.ndim
    padding[axis] = (before, after)
    padded = np.pad(values, padding, mode="reflect")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.float64)
    zeros_shape = list(cumulative.shape)
    zeros_shape[axis] = 1
    cumulative = np.concatenate((np.zeros(zeros_shape), cumulative), axis=axis)
    high = [slice(None)] * values.ndim
    low = [slice(None)] * values.ndim
    high[axis] = slice(window, None)
    low[axis] = slice(None, -window)
    return cumulative[tuple(high)] - cumulative[tuple(low)]


def masked_box_smooth(field: ArrayLike, window: int) -> NDArray[np.float64]:
    """NaN-aware separable box smoothing with the same shape as the input."""
    values = np.asarray(field, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError("Mesoscale smoothing requires a two-dimensional field")
    if window < 1:
        raise ValueError("Smoothing window must be positive")
    if window == 1:
        return values.copy()
    valid = np.isfinite(values)
    numerator = np.where(valid, values, 0.0)
    denominator = valid.astype(np.float64)
    for axis in (0, 1):
        numerator = _box_sum_axis(numerator, window, axis)
        denominator = _box_sum_axis(denominator, window, axis)
    return np.divide(
        numerator,
        denominator,
        out=np.full(values.shape, np.nan, dtype=np.float64),
        where=denominator > 0,
    )


def mesoscale_anomaly(field: ArrayLike, window: int = 33) -> NDArray[np.float64]:
    values = np.asarray(field, dtype=np.float64)
    return values - masked_box_smooth(values, window)


def _limited_anomaly(
    field: ArrayLike, window: int, cap: float
) -> tuple[NDArray[np.float64], NDArray[np.bool_], NDArray[np.bool_]]:
    raw = mesoscale_anomaly(field, window)
    limited = np.clip(raw, -cap, cap)
    available = np.isfinite(raw)
    return np.where(available, limited, 0.0), available & (raw != limited), available


def hybridize_thermodynamics(
    baseline: dict[str, ArrayLike],
    hrrr: dict[str, ArrayLike],
    weights: HybridWeights,
    *,
    window: int = 33,
    caps: HybridCaps = DEFAULT_HYBRID_CAPS,
) -> tuple[dict[str, NDArray[np.float64]], NDArray[np.uint16]]:
    """Apply HRRR high-pass terms and reconstruct one consistent thermodynamic state."""
    weights.validate()
    required = {"T2D", "PSFC", "RH2D", "Q2D", "LWDOWN"}
    if required - baseline.keys() or required - hrrr.keys():
        raise ValueError("Thermodynamic hybrid inputs are incomplete")
    base = {name: np.asarray(baseline[name], dtype=np.float64) for name in required}
    fine = {name: np.asarray(hrrr[name], dtype=np.float64) for name in required}
    shape = np.broadcast_shapes(*(value.shape for value in (*base.values(), *fine.values())))
    qc = np.zeros(shape, dtype=np.uint16)
    thermo_enabled = any(
        value != 0.0
        for value in (
            weights.temperature,
            weights.log_pressure,
            weights.relative_humidity,
            weights.log_longwave_factor,
        )
    )
    if not thermo_enabled:
        return {name: value.copy() for name, value in base.items()}, qc

    temperature_anomaly, clipped_t, available_t = _limited_anomaly(
        fine["T2D"], window, caps.temperature_k
    )
    pressure_anomaly, clipped_p, available_p = _limited_anomaly(
        np.log(fine["PSFC"]), window, caps.log_pressure
    )
    humidity_anomaly, clipped_rh, available_rh = _limited_anomaly(
        fine["RH2D"], window, caps.relative_humidity
    )
    temperature = base["T2D"] + weights.temperature * temperature_anomaly
    pressure = np.exp(np.log(base["PSFC"]) + weights.log_pressure * pressure_anomaly)
    relative_humidity = np.clip(
        base["RH2D"] + weights.relative_humidity * humidity_anomaly, 0.0, 1.0
    )
    specific_humidity = specific_humidity_from_relative_humidity(
        relative_humidity, temperature, pressure, phase="water"
    )

    base_emission = cosgrove_atmospheric_emissivity(
        base["T2D"], base["Q2D"], base["PSFC"]
    ) * np.power(base["T2D"], 4)
    fine_emission = cosgrove_atmospheric_emissivity(
        fine["T2D"], fine["Q2D"], fine["PSFC"]
    ) * np.power(fine["T2D"], 4)
    base_factor = np.divide(base["LWDOWN"], base_emission)
    fine_factor = np.divide(fine["LWDOWN"], fine_emission)
    longwave_anomaly, clipped_lw, available_lw = _limited_anomaly(
        np.log(fine_factor), window, caps.log_longwave_factor
    )
    factor = np.exp(np.log(base_factor) + weights.log_longwave_factor * longwave_anomaly)
    emission = cosgrove_atmospheric_emissivity(
        temperature, specific_humidity, pressure
    ) * np.power(temperature, 4)
    downward_longwave = factor * emission

    refined = (
        (available_t & (weights.temperature != 0.0))
        | (available_p & (weights.log_pressure != 0.0))
        | (available_rh & (weights.relative_humidity != 0.0))
        | (available_lw & (weights.log_longwave_factor != 0.0))
    )
    temperature = np.where(refined, temperature, base["T2D"])
    pressure = np.where(refined, pressure, base["PSFC"])
    relative_humidity = np.where(refined, relative_humidity, base["RH2D"])
    specific_humidity = np.where(refined, specific_humidity, base["Q2D"])
    downward_longwave = np.where(refined, downward_longwave, base["LWDOWN"])
    qc[refined] |= HYBRID_REFINEMENT_APPLIED
    clipped = clipped_t | clipped_p | clipped_rh | clipped_lw
    qc[clipped] |= HYBRID_REFINEMENT_CLIPPED
    return {
        "T2D": temperature,
        "PSFC": pressure,
        "RH2D": relative_humidity,
        "Q2D": specific_humidity,
        "LWDOWN": downward_longwave,
    }, qc


def hybridize_radiation_wind(
    baseline: dict[str, ArrayLike],
    hrrr: dict[str, ArrayLike],
    weights: HybridWeights,
    *,
    window: int = 33,
    caps: HybridCaps = DEFAULT_HYBRID_CAPS,
) -> tuple[dict[str, NDArray[np.float64]], NDArray[np.uint16]]:
    """Apply HRRR clear-sky-index and earth-relative vector anomalies."""
    weights.validate()
    required = {"SWDOWN", "U2D", "V2D", "COSZEN"}
    if required - baseline.keys() or required - hrrr.keys():
        raise ValueError("Radiation/wind hybrid inputs are incomplete")
    base = {name: np.asarray(baseline[name], dtype=np.float64) for name in required}
    fine = {name: np.asarray(hrrr[name], dtype=np.float64) for name in required}
    radiation_enabled = any(
        value != 0.0
        for value in (weights.clear_sky_index, weights.wind_u, weights.wind_v)
    )
    if not radiation_enabled:
        return (
            {name: value.copy() for name, value in base.items()},
            np.zeros(base["SWDOWN"].shape, dtype=np.uint16),
        )
    scale = 1361.0 * np.maximum(fine["COSZEN"], 0.0)
    fine_index = np.divide(
        fine["SWDOWN"],
        scale,
        out=np.full_like(scale, np.nan),
        where=(scale > 1.0) & np.isfinite(fine["SWDOWN"]),
    )
    base_scale = 1361.0 * np.maximum(base["COSZEN"], 0.0)
    base_index = np.divide(
        base["SWDOWN"], base_scale, out=np.zeros_like(base_scale), where=base_scale > 1.0
    )
    shortwave_anomaly, clipped_sw, available_sw = _limited_anomaly(
        fine_index, window, caps.clear_sky_index
    )
    u_anomaly, clipped_u, available_u = _limited_anomaly(
        fine["U2D"], window, caps.wind_m_s
    )
    v_anomaly, clipped_v, available_v = _limited_anomaly(
        fine["V2D"], window, caps.wind_m_s
    )
    clear_sky_index = np.maximum(
        0.0, base_index + weights.clear_sky_index * shortwave_anomaly
    )
    shortwave = np.where(base_scale > 1.0, clear_sky_index * base_scale, 0.0)
    eastward = base["U2D"] + weights.wind_u * u_anomaly
    northward = base["V2D"] + weights.wind_v * v_anomaly
    qc = np.zeros(shortwave.shape, dtype=np.uint16)
    refined = (
        (available_sw & (weights.clear_sky_index != 0.0))
        | (available_u & (weights.wind_u != 0.0))
        | (available_v & (weights.wind_v != 0.0))
    )
    qc[refined] |= HYBRID_REFINEMENT_APPLIED
    qc[clipped_sw | clipped_u | clipped_v] |= HYBRID_REFINEMENT_CLIPPED
    return {
        "SWDOWN": shortwave,
        "U2D": eastward,
        "V2D": northward,
        "COSZEN": base["COSZEN"],
    }, qc


def _fields(dataset: Dataset, names: set[str]) -> dict[str, NDArray[np.float64]]:
    return {
        name: np.ma.asarray(dataset[name][0]).filled(np.nan).astype(np.float64)
        for name in names
    }


def write_hybrid_components(
    baseline_thermodynamic: Path,
    hrrr_thermodynamic: Path,
    baseline_radiation_wind: Path,
    hrrr_radiation_wind: Path,
    output_thermodynamic: Path,
    output_radiation_wind: Path,
    weights: HybridWeights,
    *,
    window: int = 33,
    caps: HybridCaps = DEFAULT_HYBRID_CAPS,
) -> tuple[Path, Path]:
    """Create component files ready for the existing LDASIN assembler."""
    weights.validate()
    with (
        Dataset(baseline_thermodynamic) as base_t,
        Dataset(hrrr_thermodynamic) as fine_t,
        Dataset(baseline_radiation_wind) as base_r,
        Dataset(hrrr_radiation_wind) as fine_r,
    ):
        times = {
            data.getncattr("source_valid_time") for data in (base_t, fine_t, base_r, fine_r)
        }
        if len(times) != 1:
            raise ValueError("Hybrid component valid times differ")
        thermo, thermo_qc = hybridize_thermodynamics(
            _fields(base_t, {"T2D", "PSFC", "RH2D", "Q2D", "LWDOWN"}),
            _fields(fine_t, {"T2D", "PSFC", "RH2D", "Q2D", "LWDOWN"}),
            weights,
            window=window,
            caps=caps,
        )
        radiation, radiation_qc = hybridize_radiation_wind(
            _fields(base_r, {"SWDOWN", "U2D", "V2D", "COSZEN"}),
            _fields(fine_r, {"SWDOWN", "U2D", "V2D", "COSZEN"}),
            weights,
            window=window,
            caps=caps,
        )
    for source, destination, values, qc_name, added_qc in (
        (baseline_thermodynamic, output_thermodynamic, thermo, "thermodynamic_qc_flags", thermo_qc),
        (baseline_radiation_wind, output_radiation_wind, radiation, "radiation_wind_qc_flags", radiation_qc),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        shutil.copyfile(source, partial)
        with Dataset(partial, "a") as output:
            for name, field in values.items():
                output[name][0] = np.ma.masked_invalid(field)
            if qc_name == "thermodynamic_qc_flags" and "T2D_PRELIM" in output.variables:
                output["T2D_PRELIM"][0] = np.ma.masked_invalid(values["T2D"])
            output[qc_name][0] = np.asarray(output[qc_name][0], dtype=np.uint16) | added_qc
            output.setncattr("source_product", "nldas2_hrrr_hybrid")
            output.setncattr("baseline_component", str(source))
            output.setncattr("hrrr_component", str(
                hrrr_thermodynamic if qc_name == "thermodynamic_qc_flags" else hrrr_radiation_wind
            ))
            output.setncattr("hybrid_weights", json.dumps(asdict(weights), sort_keys=True))
            output.setncattr("hybrid_smoothing_window_cells", window)
        partial.replace(destination)
    return output_thermodynamic, output_radiation_wind
