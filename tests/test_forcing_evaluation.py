from __future__ import annotations

import numpy as np
import pytest

from hydro_ops.forcing.evaluation import (
    categorical_precipitation_metrics,
    continuous_metrics,
    deterministic_group_split,
    precipitation_threshold_sweep,
    stage4_override_sweep,
    stratified_metrics,
)


def test_continuous_metrics_and_missing_pairs() -> None:
    result = continuous_metrics(
        np.array([1.0, 2.0, 3.0, np.nan]), np.array([2.0, 2.0, 4.0, 9.0])
    )
    assert result.count == 3
    assert result.bias == pytest.approx(2 / 3)
    assert result.mae == pytest.approx(2 / 3)
    assert result.rmse == pytest.approx(np.sqrt(2 / 3))


def test_stratified_metrics() -> None:
    result = stratified_metrics(
        np.array([1, 2, 3, 4]), np.array([1, 3, 3, 6]), np.array(["low", "low", "high", "high"])
    )
    assert result["low"].bias == pytest.approx(0.5)
    assert result["high"].bias == pytest.approx(1.0)


def test_precipitation_threshold_sweep_reports_source_changes() -> None:
    results = precipitation_threshold_sweep(
        {
            "mrms_pass2": np.array([1.0, 10.0]),
            "stage4_archive": np.array([2.0, 2.0]),
        },
        np.array([0.4, 0.8]),
        np.array([2.0, 2.0]),
        [0.3, 0.5],
    )
    assert results[0]["source_counts"] == {"1": 2}
    assert results[1]["source_counts"] == {"1": 1, "3": 1}
    assert results[1]["metrics"]["rmse"] < results[0]["metrics"]["rmse"]


def test_categorical_precipitation_metrics() -> None:
    result = categorical_precipitation_metrics(
        np.array([1.0, 1.0, 0.0, 0.0]), np.array([1.0, 0.0, 1.0, 0.0])
    )
    assert result.hits == result.misses == result.false_alarms == 1
    assert result.critical_success_index == pytest.approx(1 / 3)


def test_deterministic_split_keeps_groups_together() -> None:
    groups = np.array(["event-a", "event-a", "event-b", "event-b", "event-c"])
    first = deterministic_group_split(groups, salt="fixed")
    second = deterministic_group_split(groups, salt="fixed")
    np.testing.assert_array_equal(first, second)
    assert first[0] == first[1]
    assert first[2] == first[3]


def test_stage4_override_sweep_uses_only_calibration_region() -> None:
    results = stage4_override_sweep(
        {
            "mrms_pass2": np.array([10.0, 10.0, 1.0, 1.0]),
            "stage4_archive": np.array([2.0, 2.0, 2.0, 2.0]),
        },
        np.array([0.9, 0.9, 0.9, 0.9]),
        np.array([2.0, 2.0, 2.0, 2.0]),
        np.array(["west", "west", "east", "east"]),
        quality_thresholds=[0.5],
        disagreement_thresholds=[5.0],
        calibration_mask=np.array([True, False, True, False]),
    )
    west = next(row for row in results if row["stratum"] == "west")
    east = next(row for row in results if row["stratum"] == "east")
    assert west["override_count"] == 1
    assert west["metrics"]["rmse"] == 0
    assert east["override_count"] == 0
