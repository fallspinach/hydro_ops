from __future__ import annotations

import numpy as np

from hydro_ops.forcing.monthly_prism import (
    assess_monthly_reconciliation,
    monthly_temperature_adjustment,
    reconcile_prism_month,
)
from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    ReconciliationQC,
    ReconciliationResult,
)


def test_monthly_temperature_matches_mean_daily_extrema() -> None:
    hourly = np.array(
        [
            [[270.0], [272.0]],
            [[280.0], [282.0]],
            [[274.0], [276.0]],
            [[284.0], [286.0]],
        ]
    ).reshape(2, 2, 2, 1)
    daily_minimum = hourly.min(axis=1)
    daily_maximum = hourly.max(axis=1)
    target_minimum = np.array([[275.0], [277.0]])
    target_maximum = np.array([[295.0], [297.0]])
    adjustment = monthly_temperature_adjustment(
        daily_minimum.mean(axis=0),
        daily_maximum.mean(axis=0),
        target_minimum,
        target_maximum,
        scale_bounds=(0.1, 10.0),
    )
    corrected = adjustment.apply(hourly)
    np.testing.assert_allclose(corrected.min(axis=1).mean(axis=0), target_minimum)
    np.testing.assert_allclose(corrected.max(axis=1).mean(axis=0), target_maximum)
    assert np.all(np.diff(corrected, axis=1) > 0)


def test_monthly_precipitation_factor_preserves_hourly_fractions() -> None:
    operator = ConservativeOperator(
        source_size=2,
        target_size=1,
        source_index=np.array([0, 1]),
        target_index=np.array([0, 0]),
        weight=np.ones(2),
    )
    hours = np.array([[1.0, 2.0], [3.0, 6.0], [0.0, 0.0]])
    result = reconcile_prism_month(hours.sum(axis=0), np.array([12.0]), operator)
    corrected = hours * result.correction_factor
    np.testing.assert_allclose(operator.apply(corrected.sum(axis=0)), [12.0])
    np.testing.assert_allclose(corrected[1] / corrected[0], [3.0, 3.0])


def test_monthly_acceptance_rejects_capped_and_dry_wet_population() -> None:
    flags = np.array(
        [0, ReconciliationQC.RATIO_CAPPED, ReconciliationQC.BASE_DRY_TARGET_WET],
        dtype=np.uint16,
    )
    result = ReconciliationResult(
        hourly_depth=np.ones((1, 3)),
        daily_depth=np.ones(3),
        correction_factor=np.ones(3),
        target_residual=np.array([0.0, -8.0, -4.0]),
        target_qc_flags=flags,
        iterations=1,
        converged=True,
    )
    assessment = assess_monthly_reconciliation(
        result,
        np.array([10.0, 10.0, 5.0]),
        maximum_unconverged_fraction=1.0,
        maximum_unresolved_fraction=0.5,
        maximum_capped_fraction=0.2,
        maximum_dry_baseline_wet_target_fraction=0.2,
    )
    assert not assessment.accepted
    assert assessment.unresolved_fraction == 2 / 3
    assert assessment.capped_fraction == 1 / 3
    assert assessment.dry_baseline_wet_target_fraction == 1 / 3
