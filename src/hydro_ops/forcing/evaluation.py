"""Calibration and withheld-validation metrics without fitting production parameters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256

import numpy as np

from hydro_ops.forcing.precipitation import composite_precipitation


@dataclass(frozen=True)
class ContinuousMetrics:
    count: int
    bias: float
    mae: float
    rmse: float
    correlation: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CategoricalMetrics:
    count: int
    hits: int
    misses: int
    false_alarms: int
    probability_of_detection: float
    false_alarm_ratio: float
    critical_success_index: float

    def as_dict(self) -> dict:
        return asdict(self)


def continuous_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    weights: np.ndarray | None = None,
) -> ContinuousMetrics:
    """Compute finite-pair continuous metrics with optional nonnegative weights."""
    reference, candidate = np.broadcast_arrays(
        np.asarray(reference, dtype=np.float64), np.asarray(candidate, dtype=np.float64)
    )
    valid = np.isfinite(reference) & np.isfinite(candidate)
    if weights is None:
        weight = np.ones(reference.shape, dtype=np.float64)
    else:
        weight = np.broadcast_to(np.asarray(weights, dtype=np.float64), reference.shape)
        if np.any(weight[np.isfinite(weight)] < 0):
            raise ValueError("Metric weights must be nonnegative")
        valid &= np.isfinite(weight) & (weight > 0)
    count = int(valid.sum())
    if not count:
        return ContinuousMetrics(0, np.nan, np.nan, np.nan, np.nan)
    ref = reference[valid]
    cand = candidate[valid]
    weight = weight[valid]
    difference = cand - ref
    bias = np.average(difference, weights=weight)
    mae = np.average(np.abs(difference), weights=weight)
    rmse = np.sqrt(np.average(difference**2, weights=weight))
    ref_anomaly = ref - np.average(ref, weights=weight)
    candidate_anomaly = cand - np.average(cand, weights=weight)
    denominator = np.sqrt(
        np.sum(weight * ref_anomaly**2) * np.sum(weight * candidate_anomaly**2)
    )
    correlation = (
        np.sum(weight * ref_anomaly * candidate_anomaly) / denominator
        if denominator > 0
        else np.nan
    )
    return ContinuousMetrics(count, float(bias), float(mae), float(rmse), float(correlation))


def stratified_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    strata: np.ndarray,
) -> dict[str, ContinuousMetrics]:
    """Evaluate arbitrary region/season/elevation/source labels without hard-coding them."""
    reference, candidate, strata = np.broadcast_arrays(reference, candidate, strata)
    results = {}
    for label in np.unique(strata):
        selected = strata == label
        results[str(label)] = continuous_metrics(reference[selected], candidate[selected])
    return results


def categorical_precipitation_metrics(
    reference: np.ndarray, candidate: np.ndarray, *, wet_threshold: float = 0.1
) -> CategoricalMetrics:
    """Compute wet/dry event scores from finite pairs."""
    reference, candidate = np.broadcast_arrays(
        np.asarray(reference, dtype=np.float64), np.asarray(candidate, dtype=np.float64)
    )
    valid = np.isfinite(reference) & np.isfinite(candidate)
    observed = reference[valid] >= wet_threshold
    predicted = candidate[valid] >= wet_threshold
    hits = int(np.sum(observed & predicted))
    misses = int(np.sum(observed & ~predicted))
    false_alarms = int(np.sum(~observed & predicted))

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else np.nan

    return CategoricalMetrics(
        int(valid.sum()),
        hits,
        misses,
        false_alarms,
        ratio(hits, hits + misses),
        ratio(false_alarms, hits + false_alarms),
        ratio(hits, hits + misses + false_alarms),
    )


def deterministic_group_split(
    groups: np.ndarray,
    *,
    calibration_fraction: float = 0.7,
    salt: str = "hydro-ops-precipitation-v1",
) -> np.ndarray:
    """Assign whole event/day groups to calibration (True) or withheld validation.

    Hash assignment is stable across machines and dataset growth, unlike a shuffled row split.
    """
    if not 0 < calibration_fraction < 1:
        raise ValueError("calibration_fraction must lie strictly between zero and one")
    groups = np.asarray(groups)
    result = np.zeros(groups.shape, dtype=bool)
    limit = int(calibration_fraction * 2**64)
    for label in np.unique(groups):
        digest = sha256(f"{salt}:{label}".encode()).digest()
        result[groups == label] = int.from_bytes(digest[:8], "big") < limit
    return result


def stage4_override_sweep(
    candidates: dict[str, np.ndarray],
    quality: np.ndarray,
    reference: np.ndarray,
    strata: np.ndarray,
    *,
    quality_thresholds: list[float],
    disagreement_thresholds: list[float],
    calibration_mask: np.ndarray,
) -> list[dict]:
    """Score regional Stage-IV override rules on calibration samples only."""
    if "stage4_archive" not in candidates and "stage4_realtime" not in candidates:
        raise ValueError("A Stage-IV candidate is required")
    if "mrms_pass2" not in candidates and "mrms_pass1" not in candidates:
        raise ValueError("An MRMS candidate is required")
    stage_name = "stage4_archive" if "stage4_archive" in candidates else "stage4_realtime"
    mrms_name = "mrms_pass2" if "mrms_pass2" in candidates else "mrms_pass1"
    stage = np.asarray(candidates[stage_name], dtype=np.float64)
    mrms = np.asarray(candidates[mrms_name], dtype=np.float64)
    quality, reference, strata, calibration = np.broadcast_arrays(
        quality, reference, strata, calibration_mask
    )
    disagreement = np.abs(stage - mrms)
    results: list[dict] = []
    for label in np.unique(strata):
        region = (strata == label) & calibration.astype(bool)
        for quality_threshold in quality_thresholds:
            for disagreement_threshold in disagreement_thresholds:
                override = (
                    region
                    & np.isfinite(stage)
                    & ((quality < quality_threshold) | (disagreement >= disagreement_threshold))
                )
                composite = composite_precipitation(
                    candidates,
                    mrms_quality=quality,
                    mrms_quality_threshold=0.5,
                    stage4_override=override,
                )
                selected = region & np.isfinite(reference) & np.isfinite(composite.depth)
                metrics = continuous_metrics(reference[selected], composite.depth[selected])
                categorical = categorical_precipitation_metrics(
                    reference[selected], composite.depth[selected]
                )
                results.append(
                    {
                        "stratum": str(label),
                        "quality_below": quality_threshold,
                        "absolute_disagreement_at_least": disagreement_threshold,
                        "override_count": int(override.sum()),
                        "metrics": metrics.as_dict(),
                        "categorical": categorical.as_dict(),
                    }
                )
    return results


def precipitation_threshold_sweep(
    candidates: dict[str, np.ndarray],
    quality: np.ndarray,
    reference: np.ndarray,
    thresholds: list[float],
) -> list[dict]:
    """Evaluate candidate MRMS quality thresholds without changing production settings."""
    results = []
    for threshold in thresholds:
        composite = composite_precipitation(
            candidates, mrms_quality=quality, mrms_quality_threshold=threshold
        )
        results.append(
            {
                "mrms_quality_threshold": threshold,
                "metrics": continuous_metrics(reference, composite.depth).as_dict(),
                "source_counts": {
                    str(int(source)): int(count)
                    for source, count in zip(
                        *np.unique(composite.source_id, return_counts=True), strict=True
                    )
                },
            }
        )
    return results
