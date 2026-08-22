"""Coupled temperature, pressure, humidity, and longwave transformations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag

import numpy as np
from numpy.typing import ArrayLike, NDArray

from hydro_ops.forcing.physics import (
    DEFAULT_LAPSE_RATE,
    cosgrove_atmospheric_emissivity,
    pressure_at_elevation,
    relative_humidity_from_specific_humidity,
    specific_humidity_from_relative_humidity,
    temperature_at_elevation,
)


class ThermodynamicQC(IntFlag):
    """Per-cell quality-control flags for the coupled thermodynamic bundle."""

    RH_CLIPPED_LOW = 1
    RH_CLIPPED_HIGH = 2
    INVALID_INPUT = 4
    INVALID_LONGWAVE_FACTOR = 8


@dataclass(frozen=True)
class ReferenceState:
    """Fields normalized to a common reference elevation before interpolation."""

    temperature: NDArray[np.float64]
    pressure: NDArray[np.float64]
    relative_humidity: NDArray[np.float64]
    longwave_factor: NDArray[np.float64]
    qc_flags: NDArray[np.uint16]


@dataclass(frozen=True)
class TargetState:
    """Physically consistent target-grid forcing and diagnostics."""

    temperature: NDArray[np.float64]
    preliminary_temperature: NDArray[np.float64]
    pressure: NDArray[np.float64]
    specific_humidity: NDArray[np.float64]
    relative_humidity: NDArray[np.float64]
    downward_longwave: NDArray[np.float64]
    elevation_temperature_adjustment: NDArray[np.float64]
    elevation_pressure_adjustment: NDArray[np.float64]
    qc_flags: NDArray[np.uint16]


def _array(value: ArrayLike) -> NDArray[np.float64]:
    return np.asarray(value, dtype=np.float64)


def _validate_and_clip_rh(
    relative_humidity: NDArray[np.float64],
    qc_flags: NDArray[np.uint16],
    *,
    tolerance: float,
) -> NDArray[np.float64]:
    finite = np.isfinite(relative_humidity)
    if np.any(relative_humidity[finite] < -tolerance):
        raise ValueError("Relative humidity is materially below zero")
    if np.any(relative_humidity[finite] > 1.0 + tolerance):
        raise ValueError("Relative humidity exceeds the configured supersaturation tolerance")
    low = finite & (relative_humidity < 0.0)
    high = finite & (relative_humidity > 1.0)
    qc_flags[low] |= np.uint16(ThermodynamicQC.RH_CLIPPED_LOW)
    qc_flags[high] |= np.uint16(ThermodynamicQC.RH_CLIPPED_HIGH)
    return np.clip(relative_humidity, 0.0, 1.0)


def prepare_reference_state(
    temperature: ArrayLike,
    pressure: ArrayLike,
    specific_humidity: ArrayLike,
    downward_longwave: ArrayLike,
    source_elevation: ArrayLike,
    *,
    reference_elevation: float = 0.0,
    lapse_rate: float = DEFAULT_LAPSE_RATE,
    relative_humidity_tolerance: float = 0.05,
    saturation_phase: str = "water",
) -> ReferenceState:
    """Normalize a source thermodynamic bundle to one elevation before remapping."""
    temperature = _array(temperature)
    pressure = _array(pressure)
    specific_humidity = _array(specific_humidity)
    downward_longwave = _array(downward_longwave)
    source_elevation = _array(source_elevation)
    shape = np.broadcast_shapes(
        temperature.shape,
        pressure.shape,
        specific_humidity.shape,
        downward_longwave.shape,
        source_elevation.shape,
    )
    qc_flags = np.zeros(shape, dtype=np.uint16)
    invalid = ~(
        np.isfinite(temperature)
        & np.isfinite(pressure)
        & np.isfinite(specific_humidity)
        & np.isfinite(downward_longwave)
        & np.isfinite(source_elevation)
    )
    qc_flags[invalid] |= np.uint16(ThermodynamicQC.INVALID_INPUT)
    relative_humidity = relative_humidity_from_specific_humidity(
        specific_humidity, temperature, pressure, phase=saturation_phase
    )
    relative_humidity = _validate_and_clip_rh(
        relative_humidity, qc_flags, tolerance=relative_humidity_tolerance
    )
    reference_temperature = temperature_at_elevation(
        temperature,
        source_elevation,
        reference_elevation,
        lapse_rate=lapse_rate,
    )
    reference_pressure = pressure_at_elevation(
        pressure,
        temperature,
        source_elevation,
        reference_elevation,
        lapse_rate=lapse_rate,
    )
    emissivity = cosgrove_atmospheric_emissivity(
        temperature, specific_humidity, pressure
    )
    emission = emissivity * np.power(temperature, 4)
    longwave_factor = np.divide(
        downward_longwave,
        emission,
        out=np.full(shape, np.nan, dtype=np.float64),
        where=np.isfinite(emission) & (emission > 0),
    )
    invalid_factor = ~np.isfinite(longwave_factor)
    qc_flags[invalid_factor] |= np.uint16(ThermodynamicQC.INVALID_LONGWAVE_FACTOR)
    reference_temperature = np.where(invalid, np.nan, reference_temperature)
    reference_pressure = np.where(invalid, np.nan, reference_pressure)
    relative_humidity = np.where(invalid, np.nan, relative_humidity)
    longwave_factor = np.where(invalid, np.nan, longwave_factor)
    return ReferenceState(
        reference_temperature,
        reference_pressure,
        relative_humidity,
        longwave_factor,
        qc_flags,
    )


def finalize_target_state(
    reference_temperature: ArrayLike,
    reference_pressure: ArrayLike,
    relative_humidity: ArrayLike,
    longwave_factor: ArrayLike,
    target_elevation: ArrayLike,
    *,
    final_temperature: ArrayLike | None = None,
    reference_elevation: float = 0.0,
    lapse_rate: float = DEFAULT_LAPSE_RATE,
    relative_humidity_tolerance: float = 0.05,
    saturation_phase: str = "water",
) -> TargetState:
    """Restore NWM elevation and reconstruct a consistent target thermodynamic state."""
    reference_temperature = _array(reference_temperature)
    reference_pressure = _array(reference_pressure)
    relative_humidity = _array(relative_humidity)
    longwave_factor = _array(longwave_factor)
    target_elevation = _array(target_elevation)
    shape = np.broadcast_shapes(
        reference_temperature.shape,
        reference_pressure.shape,
        relative_humidity.shape,
        longwave_factor.shape,
        target_elevation.shape,
    )
    qc_flags = np.zeros(shape, dtype=np.uint16)
    invalid = ~(
        np.isfinite(reference_temperature)
        & np.isfinite(reference_pressure)
        & np.isfinite(relative_humidity)
        & np.isfinite(longwave_factor)
        & np.isfinite(target_elevation)
    )
    qc_flags[invalid] |= np.uint16(ThermodynamicQC.INVALID_INPUT)
    relative_humidity = _validate_and_clip_rh(
        relative_humidity, qc_flags, tolerance=relative_humidity_tolerance
    )
    preliminary_temperature = temperature_at_elevation(
        reference_temperature,
        reference_elevation,
        target_elevation,
        lapse_rate=lapse_rate,
    )
    pressure = pressure_at_elevation(
        reference_pressure,
        reference_temperature,
        reference_elevation,
        target_elevation,
        lapse_rate=lapse_rate,
    )
    temperature = (
        preliminary_temperature if final_temperature is None else _array(final_temperature)
    )
    if np.broadcast_shapes(temperature.shape, shape) != shape:
        raise ValueError("Final temperature is not broadcast-compatible with the target state")
    specific_humidity = specific_humidity_from_relative_humidity(
        relative_humidity, temperature, pressure, phase=saturation_phase
    )
    emissivity = cosgrove_atmospheric_emissivity(
        temperature, specific_humidity, pressure
    )
    downward_longwave = longwave_factor * emissivity * np.power(temperature, 4)
    temperature = np.where(invalid, np.nan, temperature)
    preliminary_temperature = np.where(invalid, np.nan, preliminary_temperature)
    pressure = np.where(invalid, np.nan, pressure)
    specific_humidity = np.where(invalid, np.nan, specific_humidity)
    relative_humidity = np.where(invalid, np.nan, relative_humidity)
    downward_longwave = np.where(invalid, np.nan, downward_longwave)
    return TargetState(
        temperature,
        preliminary_temperature,
        pressure,
        specific_humidity,
        relative_humidity,
        downward_longwave,
        preliminary_temperature - reference_temperature,
        pressure - reference_pressure,
        qc_flags,
    )
