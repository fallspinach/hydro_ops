from __future__ import annotations

import numpy as np

from hydro_ops.forcing.hybrid import (
    HybridWeights,
    hybridize_radiation_wind,
    hybridize_thermodynamics,
    masked_box_smooth,
    mesoscale_anomaly,
)
from hydro_ops.forcing.physics import (
    relative_humidity_from_specific_humidity,
    specific_humidity_from_relative_humidity,
)


def test_masked_smoothing_preserves_constant_and_missing_center() -> None:
    field = np.full((7, 7), 4.0)
    field[3, 3] = np.nan
    smooth = masked_box_smooth(field, 3)
    np.testing.assert_allclose(smooth, 4.0)
    anomaly = mesoscale_anomaly(field, 3)
    np.testing.assert_allclose(anomaly[np.isfinite(anomaly)], 0.0)
    assert np.isnan(anomaly[3, 3])


def _thermodynamic_fields() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    shape = (9, 9)
    temperature = np.full(shape, 290.0)
    pressure = np.full(shape, 95_000.0)
    humidity = np.full(shape, 0.5)
    baseline = {
        "T2D": temperature,
        "PSFC": pressure,
        "RH2D": humidity,
        "Q2D": specific_humidity_from_relative_humidity(
            humidity, temperature, pressure, phase="water"
        ),
        "LWDOWN": np.full(shape, 320.0),
    }
    hrrr = {name: values.copy() for name, values in baseline.items()}
    hrrr["T2D"][4, 4] += 4.0
    hrrr["PSFC"][4, 4] *= 1.02
    hrrr["RH2D"][4, 4] += 0.2
    hrrr["LWDOWN"][4, 4] += 30.0
    return baseline, hrrr


def test_zero_weights_reproduce_nldas_thermodynamics_exactly() -> None:
    baseline, hrrr = _thermodynamic_fields()
    result, qc = hybridize_thermodynamics(baseline, hrrr, HybridWeights(), window=3)
    for name in ("T2D", "PSFC", "RH2D", "LWDOWN"):
        np.testing.assert_allclose(result[name], baseline[name])
    np.testing.assert_allclose(
        relative_humidity_from_specific_humidity(
            result["Q2D"], result["T2D"], result["PSFC"], phase="water"
        ),
        result["RH2D"],
    )
    assert not np.any(qc)


def test_enabled_thermodynamic_refinement_is_local_and_consistent() -> None:
    baseline, hrrr = _thermodynamic_fields()
    weights = HybridWeights(
        temperature=0.5,
        log_pressure=0.5,
        relative_humidity=0.5,
        log_longwave_factor=0.5,
    )
    result, qc = hybridize_thermodynamics(baseline, hrrr, weights, window=3)
    assert result["T2D"][4, 4] > baseline["T2D"][4, 4]
    assert result["PSFC"][4, 4] > baseline["PSFC"][4, 4]
    assert result["RH2D"][4, 4] > baseline["RH2D"][4, 4]
    np.testing.assert_allclose(
        relative_humidity_from_specific_humidity(
            result["Q2D"], result["T2D"], result["PSFC"], phase="water"
        ),
        result["RH2D"],
    )
    assert np.all(qc != 0)


def test_radiation_and_wind_use_clear_sky_and_vector_anomalies() -> None:
    shape = (9, 9)
    baseline = {
        "SWDOWN": np.full(shape, 500.0),
        "U2D": np.full(shape, 2.0),
        "V2D": np.full(shape, 1.0),
        "COSZEN": np.full(shape, 0.7),
    }
    hrrr = {name: values.copy() for name, values in baseline.items()}
    hrrr["SWDOWN"][4, 4] += 100.0
    hrrr["U2D"][4, 4] += 4.0
    hrrr["V2D"][4, 4] -= 2.0
    result, qc = hybridize_radiation_wind(
        baseline,
        hrrr,
        HybridWeights(clear_sky_index=0.5, wind_u=0.5, wind_v=0.5),
        window=3,
    )
    assert result["SWDOWN"][4, 4] > 500.0
    assert result["U2D"][4, 4] > 2.0
    assert result["V2D"][4, 4] < 1.0
    assert np.all(qc != 0)


def test_missing_hrrr_cells_fall_back_to_nldas_values() -> None:
    baseline, hrrr = _thermodynamic_fields()
    for values in hrrr.values():
        values[0, 0] = np.nan
    result, qc = hybridize_thermodynamics(
        baseline,
        hrrr,
        HybridWeights(temperature=0.5, log_pressure=0.5, relative_humidity=0.5),
        window=3,
    )
    for name in ("T2D", "PSFC", "RH2D", "Q2D", "LWDOWN"):
        assert result[name][0, 0] == baseline[name][0, 0]
    assert qc[0, 0] == 0
