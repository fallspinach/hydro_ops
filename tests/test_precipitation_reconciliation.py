from __future__ import annotations

from pathlib import Path

import numpy as np

from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    ReconciliationQC,
    build_nwm_to_prism_weight_command,
    reconcile_prism_day,
)


def _two_region_operator() -> ConservativeOperator:
    return ConservativeOperator(
        source_size=4,
        target_size=2,
        source_index=np.array([0, 1, 2, 3]),
        target_index=np.array([0, 0, 1, 1]),
        weight=np.ones(4),
    )


def test_reverse_weight_command() -> None:
    assert build_nwm_to_prism_weight_command(
        "/bin/cdo", Path("nwm.nc"), Path("prism.nc"), Path("weights.nc")
    ) == [
        "/bin/cdo",
        "-O",
        "gencon,prism.nc",
        "-selname,active_domain",
        "nwm.nc",
        "weights.nc",
    ]


def test_reconciliation_meets_disjoint_conservative_constraints() -> None:
    hourly = np.zeros((24, 2, 2))
    hourly[0] = [[1, 1], [2, 2]]
    result = reconcile_prism_day(hourly, np.array([[4.0, 1.0]]), _two_region_operator())
    assert result.converged
    np.testing.assert_allclose(result.daily_depth, [[4, 4], [1, 1]])
    np.testing.assert_allclose(
        _two_region_operator().apply(result.daily_depth), [4, 1], atol=1e-8
    )
    np.testing.assert_allclose(result.hourly_depth.sum(axis=0), result.daily_depth)


def test_dry_baseline_wet_prism_uses_flagged_synthetic_timing() -> None:
    hourly = np.zeros((24, 2, 2))
    hourly[3, 0, :] = 1
    result = reconcile_prism_day(
        hourly, np.array([[2.0, 3.0]]), _two_region_operator(), allow_synthetic_timing=True
    )
    assert result.converged
    assert result.target_qc_flags[0, 1] & ReconciliationQC.BASE_DRY_TARGET_WET
    assert result.target_qc_flags[0, 1] & ReconciliationQC.SYNTHETIC_TIMING
    np.testing.assert_allclose(result.hourly_depth[:, 1, :].sum(axis=0), [3, 3])
    assert result.hourly_depth[3, 1, 0] == 3


def test_missing_prism_target_is_left_unconstrained() -> None:
    hourly = np.zeros((24, 2, 2))
    hourly[0] = [[1, 1], [2, 2]]
    result = reconcile_prism_day(hourly, np.array([[4.0, np.nan]]), _two_region_operator())
    np.testing.assert_allclose(result.daily_depth[1], [2, 2])
    assert result.target_qc_flags[0, 1] & ReconciliationQC.PRISM_MISSING


def test_wet_seed_does_not_create_rain_in_unmapped_source_cells() -> None:
    operator = ConservativeOperator(
        source_size=3,
        target_size=1,
        source_index=np.array([0, 1]),
        target_index=np.array([0, 0]),
        weight=np.ones(2),
    )
    hourly = np.zeros((24, 3))
    result = reconcile_prism_day(hourly, np.array([2.0]), operator, allow_synthetic_timing=True)
    assert result.daily_depth[2] == 0


def test_dry_baseline_wet_prism_is_not_synthesized_by_default() -> None:
    hourly = np.zeros((24, 2, 2))
    hourly[3, 0, :] = 1
    result = reconcile_prism_day(hourly, np.array([[2.0, 3.0]]), _two_region_operator())
    assert result.converged
    assert result.target_qc_flags[0, 1] & ReconciliationQC.BASE_DRY_TARGET_WET
    assert not result.target_qc_flags[0, 1] & ReconciliationQC.SYNTHETIC_TIMING
    np.testing.assert_array_equal(result.hourly_depth[:, 1, :], 0)


def test_cumulative_factor_and_daily_depth_are_bounded() -> None:
    hourly = np.zeros((24, 2, 2))
    hourly[0] = 1
    result = reconcile_prism_day(
        hourly,
        np.array([[1000.0, 1000.0]]),
        _two_region_operator(),
        cumulative_ratio_bounds=(0.0, 3.0),
        maximum_daily_depth=2.0,
    )
    assert result.converged
    assert np.max(result.daily_depth) <= 2.0
    assert np.all(result.target_qc_flags & ReconciliationQC.RATIO_CAPPED)
