from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from hydro_ops.forcing.physics import (
    adjust_temperature_range,
    cosgrove_atmospheric_emissivity,
    cosgrove_longwave_at_target,
    cosine_solar_zenith,
    lambert_grid_x_angle,
    pressure_at_elevation,
    relative_humidity_from_specific_humidity,
    rotate_grid_to_earth,
    saturation_vapor_pressure,
    specific_humidity_from_relative_humidity,
    temperature_at_elevation,
)


def test_temperature_lapse_rate_has_correct_sign() -> None:
    result = temperature_at_elevation(290.0, 500.0, np.array([1500.0, 0.0]))
    np.testing.assert_allclose(result, [283.5, 293.25])


def test_pressure_decreases_hydrostatically_with_elevation() -> None:
    pressure = pressure_at_elevation(100_000.0, 288.15, 0.0, 1000.0)
    assert 88_000.0 < pressure < 89_500.0
    restored = pressure_at_elevation(pressure, 281.65, 1000.0, 0.0)
    assert restored == pytest.approx(100_000.0)


def test_isothermal_pressure_limit() -> None:
    pressure = pressure_at_elevation(100_000.0, 288.15, 0.0, 1000.0, lapse_rate=0.0)
    assert 88_000.0 < pressure < 90_000.0


def test_saturation_vapor_pressure_water_and_ice() -> None:
    assert saturation_vapor_pressure(273.15, phase="water") == pytest.approx(611.2)
    assert saturation_vapor_pressure(253.15, phase="ice") < saturation_vapor_pressure(
        253.15, phase="water"
    )


def test_humidity_round_trip_after_elevation_adjustment() -> None:
    source_temperature = np.array([295.0, 275.0])
    source_pressure = np.array([95_000.0, 80_000.0])
    source_q = np.array([0.012, 0.003])
    relative_humidity = relative_humidity_from_specific_humidity(
        source_q, source_temperature, source_pressure
    )
    reconstructed = specific_humidity_from_relative_humidity(
        relative_humidity, source_temperature, source_pressure
    )
    np.testing.assert_allclose(reconstructed, source_q, rtol=1e-14)

    target_temperature = temperature_at_elevation(source_temperature, 500.0, 1500.0)
    target_pressure = pressure_at_elevation(source_pressure, source_temperature, 500.0, 1500.0)
    target_q = specific_humidity_from_relative_humidity(
        relative_humidity, target_temperature, target_pressure
    )
    target_rh = relative_humidity_from_specific_humidity(
        target_q, target_temperature, target_pressure
    )
    np.testing.assert_allclose(target_rh, relative_humidity, rtol=1e-14)
    assert np.all(target_q < source_q)


def test_temperature_range_adjustment_matches_unclipped_extrema() -> None:
    hourly = np.array([280.0, 282.0, 286.0, 284.0])[:, None]
    result = adjust_temperature_range(
        hourly,
        np.array([278.0]),
        np.array([290.0]),
        scale_bounds=None,
    )
    np.testing.assert_allclose(result.temperature.min(axis=0), [278.0])
    np.testing.assert_allclose(result.temperature.max(axis=0), [290.0])
    np.testing.assert_allclose(result.midpoint_shift, [1.0])
    np.testing.assert_allclose(result.range_scale, [2.0])
    assert not result.used_midpoint_only[0]


def test_temperature_range_safeguards_flat_curve_and_clips_scale() -> None:
    hourly = np.array([[280.0, 280.0], [280.1, 281.0], [280.0, 282.0]])
    result = adjust_temperature_range(
        hourly,
        np.array([275.0, 270.0]),
        np.array([285.0, 290.0]),
        minimum_baseline_range=0.5,
        scale_bounds=(0.25, 4.0),
    )
    assert result.used_midpoint_only[0]
    assert result.range_scale[0] == 1.0
    assert result.scale_was_clipped[1]
    assert result.range_scale[1] == 4.0


def test_grid_to_earth_rotation() -> None:
    eastward, northward = rotate_grid_to_earth(1.0, 0.0, np.pi / 2.0)
    assert eastward == pytest.approx(0.0, abs=1e-15)
    assert northward == pytest.approx(1.0)


def test_hrrr_lambert_rotation_angle() -> None:
    angle = lambert_grid_x_angle(
        np.array([-107.5, -97.5, -87.5]), -97.5, 38.5, 38.5
    )
    assert angle[1] == pytest.approx(0.0)
    assert angle[0] > 0
    assert angle[2] < 0
    expected = np.deg2rad(10 * np.sin(np.deg2rad(38.5)))
    np.testing.assert_allclose(abs(angle[[0, 2]]), expected)


def test_cosine_solar_zenith_at_equinox_greenwich() -> None:
    noon = cosine_solar_zenith(datetime(2024, 3, 20, 12, tzinfo=UTC), 0.0, 0.0)
    midnight = cosine_solar_zenith(datetime(2024, 3, 20, 0, tzinfo=UTC), 0.0, 0.0)
    assert noon > 0.99
    assert midnight < -0.99


def test_cosgrove_emissivity_matches_published_equation() -> None:
    temperature = 285.0
    humidity = 0.006
    pressure = 90_000.0
    vapor_pressure_hpa = humidity * pressure / (0.622 * 100.0)
    expected = 1.08 * (1.0 - np.exp(-(vapor_pressure_hpa ** (temperature / 2016.0))))
    assert cosgrove_atmospheric_emissivity(temperature, humidity, pressure) == pytest.approx(
        expected
    )


def test_cosgrove_longwave_identity_and_elevation_adjustment() -> None:
    source_longwave = 320.0
    source_temperature = 290.0
    source_pressure = 95_000.0
    source_humidity = 0.008
    identity = cosgrove_longwave_at_target(
        source_longwave,
        source_temperature,
        source_humidity,
        source_pressure,
        source_temperature,
        source_humidity,
        source_pressure,
    )
    assert identity == pytest.approx(source_longwave)

    relative_humidity = relative_humidity_from_specific_humidity(
        source_humidity, source_temperature, source_pressure
    )
    target_temperature = temperature_at_elevation(source_temperature, 0.0, 1000.0)
    target_pressure = pressure_at_elevation(source_pressure, source_temperature, 0.0, 1000.0)
    target_humidity = specific_humidity_from_relative_humidity(
        relative_humidity, target_temperature, target_pressure
    )
    adjusted = cosgrove_longwave_at_target(
        source_longwave,
        source_temperature,
        source_humidity,
        source_pressure,
        target_temperature,
        target_humidity,
        target_pressure,
    )
    assert 250.0 < adjusted < source_longwave


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (temperature_at_elevation, (-1.0, 0.0, 1.0)),
        (pressure_at_elevation, (0.0, 280.0, 0.0, 1.0)),
        (specific_humidity_from_relative_humidity, (-0.1, 280.0, 90_000.0)),
    ],
)
def test_physics_rejects_invalid_inputs(function, args) -> None:
    with pytest.raises(ValueError):
        function(*args)
