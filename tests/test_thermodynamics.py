from __future__ import annotations

import numpy as np
import pytest

from hydro_ops.forcing.physics import relative_humidity_from_specific_humidity
from hydro_ops.forcing.thermodynamics import (
    ThermodynamicQC,
    finalize_target_state,
    prepare_reference_state,
)


def test_reference_round_trip_at_same_elevation() -> None:
    temperature = np.array([285.0, 295.0])
    pressure = np.array([90_000.0, 95_000.0])
    humidity = np.array([0.004, 0.010])
    longwave = np.array([280.0, 350.0])
    elevation = np.array([1000.0, 500.0])
    reference = prepare_reference_state(
        temperature, pressure, humidity, longwave, elevation
    )
    target = finalize_target_state(
        reference.temperature,
        reference.pressure,
        reference.relative_humidity,
        reference.longwave_factor,
        elevation,
    )
    np.testing.assert_allclose(target.temperature, temperature)
    np.testing.assert_allclose(target.pressure, pressure)
    np.testing.assert_allclose(target.specific_humidity, humidity, rtol=1e-14)
    np.testing.assert_allclose(target.downward_longwave, longwave, rtol=1e-14)
    assert not target.qc_flags.any()


def test_target_elevation_preserves_rh_and_changes_coupled_fields() -> None:
    reference = prepare_reference_state(290.0, 95_000.0, 0.008, 320.0, 0.0)
    target = finalize_target_state(
        reference.temperature,
        reference.pressure,
        reference.relative_humidity,
        reference.longwave_factor,
        1000.0,
    )
    assert target.temperature == pytest.approx(283.5)
    assert target.pressure < 95_000.0
    assert target.specific_humidity < 0.008
    assert target.downward_longwave < 320.0
    reconstructed_rh = relative_humidity_from_specific_humidity(
        target.specific_humidity, target.temperature, target.pressure
    )
    assert reconstructed_rh == pytest.approx(reference.relative_humidity)


def test_final_temperature_override_recomputes_humidity_and_longwave_not_pressure() -> None:
    reference = prepare_reference_state(290.0, 95_000.0, 0.008, 320.0, 0.0)
    baseline = finalize_target_state(
        reference.temperature,
        reference.pressure,
        reference.relative_humidity,
        reference.longwave_factor,
        1000.0,
    )
    corrected = finalize_target_state(
        reference.temperature,
        reference.pressure,
        reference.relative_humidity,
        reference.longwave_factor,
        1000.0,
        final_temperature=baseline.temperature + 2.0,
    )
    assert corrected.pressure == pytest.approx(baseline.pressure)
    assert corrected.specific_humidity > baseline.specific_humidity
    assert corrected.downward_longwave > baseline.downward_longwave


def test_small_supersaturation_is_clipped_and_flagged() -> None:
    reference = finalize_target_state(
        290.0,
        95_000.0,
        1.01,
        1e-8,
        0.0,
    )
    assert reference.relative_humidity == pytest.approx(1.0)
    assert reference.qc_flags & ThermodynamicQC.RH_CLIPPED_HIGH


def test_material_supersaturation_is_rejected() -> None:
    with pytest.raises(ValueError, match="supersaturation"):
        finalize_target_state(290.0, 95_000.0, 1.06, 1e-8, 0.0)


def test_nan_input_propagates_and_sets_qc() -> None:
    reference = prepare_reference_state(
        np.array([290.0, np.nan]),
        95_000.0,
        0.008,
        320.0,
        0.0,
    )
    assert np.isnan(reference.temperature[1])
    assert reference.qc_flags[1] & ThermodynamicQC.INVALID_INPUT
