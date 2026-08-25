from __future__ import annotations

import numpy as np

from hydro_ops.forcing.hybrid_calibration import (
    fit_anomaly_weight,
    fit_hierarchical_weights,
    fit_vector_anomaly_weight,
)


def test_anomaly_weight_recovers_known_coefficient() -> None:
    anomaly = np.linspace(-3.0, 3.0, 101)
    baseline = 280.0 + 0.1 * anomaly
    observation = baseline + 0.6 * anomaly
    np.testing.assert_allclose(fit_anomaly_weight(observation, baseline, anomaly), 0.6)


def test_anomaly_weight_is_bounded_and_ignores_missing_values() -> None:
    assert fit_anomaly_weight([3.0, np.nan], [1.0, 1.0], [1.0, 1.0]) == 1.0
    assert fit_anomaly_weight([-3.0], [1.0], [1.0]) == 0.0


def test_vector_fit_uses_one_shared_weight() -> None:
    anomaly_u = np.array([1.0, -2.0, 3.0])
    anomaly_v = np.array([-1.0, 1.5, 2.0])
    baseline_u = np.zeros(3)
    baseline_v = np.ones(3)
    fitted = fit_vector_anomaly_weight(
        baseline_u + 0.4 * anomaly_u,
        baseline_v + 0.4 * anomaly_v,
        baseline_u,
        baseline_v,
        anomaly_u,
        anomaly_v,
    )
    np.testing.assert_allclose(fitted, 0.4)


def test_sparse_children_shrink_toward_season_parent() -> None:
    anomaly = np.ones(12)
    baseline = np.zeros(12)
    observation = np.array([0.8] * 10 + [0.0] * 2)
    season = np.array(["winter"] * 12)
    terrain = np.array(["low"] * 10 + ["high"] * 2)
    fits = fit_hierarchical_weights(
        observation,
        baseline,
        anomaly,
        season,
        terrain,
        parent_prior_samples=0,
        child_prior_samples=10,
    )
    by_label = {fit.label: fit for fit in fits}
    assert by_label["winter/high"].unregularized_weight == 0.0
    assert 0.0 < by_label["winter/high"].weight < by_label["winter"].weight
    assert by_label["winter/low"].hybrid.rmse < by_label["winter/low"].baseline.rmse
