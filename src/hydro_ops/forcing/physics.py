"""Vectorized, side-effect-free meteorological forcing transformations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from numpy.typing import ArrayLike, NDArray

DEFAULT_LAPSE_RATE = -0.0065  # K m-1
DRY_AIR_GAS_CONSTANT = 287.05  # J kg-1 K-1
WATER_VAPOR_GAS_CONSTANT = 461.5  # J kg-1 K-1
GRAVITY = 9.80665  # m s-2
EPSILON = DRY_AIR_GAS_CONSTANT / WATER_VAPOR_GAS_CONSTANT


def _float_array(value: ArrayLike) -> NDArray[np.float64]:
    return np.asarray(value, dtype=np.float64)


def temperature_at_elevation(
    temperature: ArrayLike,
    source_elevation: ArrayLike,
    target_elevation: ArrayLike,
    *,
    lapse_rate: float = DEFAULT_LAPSE_RATE,
) -> NDArray[np.float64]:
    """Translate air temperature between elevations with a fixed lapse rate."""
    temperature = _float_array(temperature)
    if np.any(temperature <= 0):
        raise ValueError("Temperature must be positive in kelvin")
    return temperature + lapse_rate * (
        _float_array(target_elevation) - _float_array(source_elevation)
    )


def pressure_at_elevation(
    pressure: ArrayLike,
    temperature: ArrayLike,
    source_elevation: ArrayLike,
    target_elevation: ArrayLike,
    *,
    lapse_rate: float = DEFAULT_LAPSE_RATE,
    gas_constant: float = DRY_AIR_GAS_CONSTANT,
    gravity: float = GRAVITY,
) -> NDArray[np.float64]:
    """Hydrostatically translate pressure using an ideal-gas lapse-rate atmosphere."""
    pressure = _float_array(pressure)
    temperature = _float_array(temperature)
    delta_z = _float_array(target_elevation) - _float_array(source_elevation)
    if np.any(pressure <= 0):
        raise ValueError("Pressure must be positive")
    if np.any(temperature <= 0):
        raise ValueError("Temperature must be positive in kelvin")
    if abs(lapse_rate) < 1e-12:
        return pressure * np.exp(-gravity * delta_z / (gas_constant * temperature))
    target_temperature = temperature + lapse_rate * delta_z
    if np.any(target_temperature <= 0):
        raise ValueError("Elevation adjustment produced nonpositive temperature")
    exponent = -gravity / (gas_constant * lapse_rate)
    return pressure * np.power(target_temperature / temperature, exponent)


def saturation_vapor_pressure(
    temperature: ArrayLike, *, phase: str = "auto"
) -> NDArray[np.float64]:
    """Return saturation vapor pressure in Pa using Magnus water/ice formulas."""
    temperature = _float_array(temperature)
    if np.any(temperature <= 0):
        raise ValueError("Temperature must be positive in kelvin")
    if phase not in {"auto", "water", "ice"}:
        raise ValueError("phase must be 'auto', 'water', or 'ice'")
    temperature_c = temperature - 273.15
    water = 611.2 * np.exp(17.67 * temperature_c / (temperature_c + 243.5))
    ice = 611.15 * np.exp(22.452 * temperature_c / (temperature_c + 272.55))
    if phase == "water":
        return water
    if phase == "ice":
        return ice
    return np.where(temperature >= 273.15, water, ice)


def vapor_pressure_from_specific_humidity(
    specific_humidity: ArrayLike, pressure: ArrayLike
) -> NDArray[np.float64]:
    """Convert specific humidity to vapor pressure without a mixing-ratio approximation."""
    specific_humidity = _float_array(specific_humidity)
    pressure = _float_array(pressure)
    if np.any((specific_humidity < 0) | (specific_humidity >= 1)):
        raise ValueError("Specific humidity must be in [0, 1)")
    if np.any(pressure <= 0):
        raise ValueError("Pressure must be positive")
    return specific_humidity * pressure / (
        EPSILON + (1.0 - EPSILON) * specific_humidity
    )


def specific_humidity_from_vapor_pressure(
    vapor_pressure: ArrayLike, pressure: ArrayLike
) -> NDArray[np.float64]:
    """Convert vapor pressure to specific humidity exactly."""
    vapor_pressure = _float_array(vapor_pressure)
    pressure = _float_array(pressure)
    if np.any(vapor_pressure < 0):
        raise ValueError("Vapor pressure must be nonnegative")
    if np.any(pressure <= vapor_pressure):
        raise ValueError("Total pressure must exceed vapor pressure")
    return EPSILON * vapor_pressure / (pressure - (1.0 - EPSILON) * vapor_pressure)


def relative_humidity_from_specific_humidity(
    specific_humidity: ArrayLike,
    temperature: ArrayLike,
    pressure: ArrayLike,
    *,
    phase: str = "auto",
) -> NDArray[np.float64]:
    """Diagnose relative humidity as a fraction from q, temperature, and pressure."""
    vapor_pressure = vapor_pressure_from_specific_humidity(specific_humidity, pressure)
    return vapor_pressure / saturation_vapor_pressure(temperature, phase=phase)


def specific_humidity_from_relative_humidity(
    relative_humidity: ArrayLike,
    temperature: ArrayLike,
    pressure: ArrayLike,
    *,
    phase: str = "auto",
) -> NDArray[np.float64]:
    """Reconstruct specific humidity while preserving relative humidity."""
    relative_humidity = _float_array(relative_humidity)
    if np.any(relative_humidity < 0):
        raise ValueError("Relative humidity must be nonnegative")
    vapor_pressure = relative_humidity * saturation_vapor_pressure(temperature, phase=phase)
    return specific_humidity_from_vapor_pressure(vapor_pressure, pressure)


def cosgrove_atmospheric_emissivity(
    temperature: ArrayLike, specific_humidity: ArrayLike, pressure: ArrayLike
) -> NDArray[np.float64]:
    """Diagnose emissivity using equation 15/16 of Cosgrove et al. (2003).

    The published method computes vapor pressure as ``q * p / 0.622`` in hPa and then
    evaluates ``1.08 * (1 - exp(-(e ** (T / 2016))))``.
    """
    temperature = _float_array(temperature)
    specific_humidity = _float_array(specific_humidity)
    pressure = _float_array(pressure)
    if np.any(temperature <= 0):
        raise ValueError("Temperature must be positive in kelvin")
    if np.any((specific_humidity < 0) | (specific_humidity >= 1)):
        raise ValueError("Specific humidity must be in [0, 1)")
    if np.any(pressure <= 0):
        raise ValueError("Pressure must be positive")
    vapor_pressure_hpa = specific_humidity * pressure / (0.622 * 100.0)
    return 1.08 * (1.0 - np.exp(-np.power(vapor_pressure_hpa, temperature / 2016.0)))


def cosgrove_longwave_at_target(
    source_longwave: ArrayLike,
    source_temperature: ArrayLike,
    source_specific_humidity: ArrayLike,
    source_pressure: ArrayLike,
    target_temperature: ArrayLike,
    target_specific_humidity: ArrayLike,
    target_pressure: ArrayLike,
) -> NDArray[np.float64]:
    """Adjust incident longwave using equations 14-18 of Cosgrove et al. (2003)."""
    source_longwave = _float_array(source_longwave)
    source_temperature = _float_array(source_temperature)
    target_temperature = _float_array(target_temperature)
    if np.any(source_longwave < 0):
        raise ValueError("Downward longwave radiation must be nonnegative")
    source_emissivity = cosgrove_atmospheric_emissivity(
        source_temperature, source_specific_humidity, source_pressure
    )
    target_emissivity = cosgrove_atmospheric_emissivity(
        target_temperature, target_specific_humidity, target_pressure
    )
    source_emission = source_emissivity * np.power(source_temperature, 4)
    target_emission = target_emissivity * np.power(target_temperature, 4)
    if np.any(source_emission <= 0):
        raise ValueError("Source atmospheric emission must be positive")
    return source_longwave * target_emission / source_emission


@dataclass(frozen=True)
class TemperatureRangeAdjustment:
    """Corrected hourly temperature and daily correction diagnostics."""

    temperature: NDArray[np.float64]
    midpoint_shift: NDArray[np.float64]
    range_scale: NDArray[np.float64]
    used_midpoint_only: NDArray[np.bool_]
    scale_was_clipped: NDArray[np.bool_]


def adjust_temperature_range(
    hourly_temperature: ArrayLike,
    prism_minimum: ArrayLike,
    prism_maximum: ArrayLike,
    *,
    axis: int = 0,
    minimum_baseline_range: float = 0.5,
    scale_bounds: tuple[float, float] | None = (0.25, 4.0),
) -> TemperatureRangeAdjustment:
    """Shift and scale an hourly curve toward daily PRISM Tmin and Tmax."""
    hourly = _float_array(hourly_temperature)
    prism_minimum = _float_array(prism_minimum)
    prism_maximum = _float_array(prism_maximum)
    if np.any(prism_maximum < prism_minimum):
        raise ValueError("PRISM maximum temperature must not be below minimum")
    baseline_minimum = np.min(hourly, axis=axis)
    baseline_maximum = np.max(hourly, axis=axis)
    baseline_midpoint = (baseline_minimum + baseline_maximum) / 2.0
    baseline_range = baseline_maximum - baseline_minimum
    prism_midpoint = (prism_minimum + prism_maximum) / 2.0
    prism_range = prism_maximum - prism_minimum
    midpoint_shift = prism_midpoint - baseline_midpoint
    midpoint_only = baseline_range < minimum_baseline_range
    safe_range = np.where(midpoint_only, 1.0, baseline_range)
    raw_scale = np.where(midpoint_only, 1.0, prism_range / safe_range)
    if scale_bounds is None:
        scale = raw_scale
        clipped = np.zeros_like(raw_scale, dtype=bool)
    else:
        lower, upper = scale_bounds
        if lower <= 0 or upper < lower:
            raise ValueError("Invalid scale bounds")
        scale = np.clip(raw_scale, lower, upper)
        clipped = scale != raw_scale
    expanded_midpoint = np.expand_dims(baseline_midpoint, axis)
    expanded_scale = np.expand_dims(scale, axis)
    expanded_target = np.expand_dims(prism_midpoint, axis)
    corrected = expanded_target + expanded_scale * (hourly - expanded_midpoint)
    return TemperatureRangeAdjustment(
        corrected,
        midpoint_shift,
        scale,
        midpoint_only,
        clipped,
    )


def rotate_grid_to_earth(
    grid_u: ArrayLike, grid_v: ArrayLike, grid_x_angle: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Rotate grid-relative vectors when grid-x is counterclockwise from east."""
    grid_u = _float_array(grid_u)
    grid_v = _float_array(grid_v)
    angle = _float_array(grid_x_angle)
    cosine = np.cos(angle)
    sine = np.sin(angle)
    eastward = grid_u * cosine - grid_v * sine
    northward = grid_u * sine + grid_v * cosine
    return eastward, northward


def lambert_grid_x_angle(
    longitude: ArrayLike,
    central_longitude: float,
    standard_parallel_1: float,
    standard_parallel_2: float | None = None,
) -> NDArray[np.float64]:
    """Return the grid-x angle from east for a north-pole Lambert conformal grid."""
    longitude = _float_array(longitude)
    phi_1 = np.deg2rad(standard_parallel_1)
    phi_2 = np.deg2rad(
        standard_parallel_1 if standard_parallel_2 is None else standard_parallel_2
    )
    if np.isclose(phi_1, phi_2):
        cone = np.sin(phi_1)
    else:
        cone = np.log(np.cos(phi_1) / np.cos(phi_2)) / np.log(
            np.tan(np.pi / 4 + phi_2 / 2) / np.tan(np.pi / 4 + phi_1 / 2)
        )
    delta_longitude = (longitude - central_longitude + 180.0) % 360.0 - 180.0
    # Meridian convergence is positive east of the central meridian. Grid x is
    # clockwise from true east there, hence the negative sign.
    return -cone * np.deg2rad(delta_longitude)


def cosine_solar_zenith(
    valid_time: datetime, latitude: ArrayLike, longitude: ArrayLike
) -> NDArray[np.float64]:
    """Approximate cosine of solar zenith using NOAA's fractional-year equations."""
    if valid_time.tzinfo is None:
        valid_time = valid_time.replace(tzinfo=UTC)
    else:
        valid_time = valid_time.astimezone(UTC)
    latitude = np.deg2rad(_float_array(latitude))
    longitude = _float_array(longitude)
    days = 366 if valid_time.year % 4 == 0 and (
        valid_time.year % 100 != 0 or valid_time.year % 400 == 0
    ) else 365
    utc_hour = (
        valid_time.hour
        + valid_time.minute / 60.0
        + valid_time.second / 3600.0
        + valid_time.microsecond / 3.6e9
    )
    gamma = 2.0 * np.pi / days * (valid_time.timetuple().tm_yday - 1 + (utc_hour - 12) / 24)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )
    solar_minutes = (utc_hour * 60.0 + equation_of_time + 4.0 * longitude) % 1440.0
    hour_angle = np.deg2rad(solar_minutes / 4.0 - 180.0)
    return np.sin(latitude) * np.sin(declination) + np.cos(latitude) * np.cos(
        declination
    ) * np.cos(hour_angle)
