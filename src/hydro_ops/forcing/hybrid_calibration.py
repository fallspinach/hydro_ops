"""Constrained, hierarchical calibration of HRRR anomaly weights."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from hydro_ops.forcing.evaluation import ContinuousMetrics, continuous_metrics


@dataclass(frozen=True)
class WeightFit:
    """One fitted anomaly weight and its before/after diagnostics."""

    label: str
    parent: str | None
    count: int
    weight: float
    unregularized_weight: float
    prior_weight: float
    baseline: ContinuousMetrics
    hybrid: ContinuousMetrics

    def as_dict(self) -> dict:
        result = asdict(self)
        result["baseline"] = self.baseline.as_dict()
        result["hybrid"] = self.hybrid.as_dict()
        return result


def _arrays(*values: ArrayLike) -> tuple[NDArray[np.float64], ...]:
    return tuple(
        np.asarray(value, dtype=np.float64)
        for value in np.broadcast_arrays(*(np.asarray(value) for value in values))
    )


def fit_anomaly_weight(
    observation: ArrayLike,
    baseline: ArrayLike,
    anomaly: ArrayLike,
    *,
    prior_weight: float = 0.0,
    prior_equivalent_samples: float = 0.0,
    sample_weight: ArrayLike | None = None,
) -> float:
    """Fit ``observation = baseline + weight * anomaly`` with weight in [0, 1].

    The ridge penalty is scaled by the mean anomaly energy, making
    ``prior_equivalent_samples`` comparable across meteorological variables.
    """
    if not 0.0 <= prior_weight <= 1.0:
        raise ValueError("Prior weight must lie in [0, 1]")
    if prior_equivalent_samples < 0.0:
        raise ValueError("Prior equivalent samples must be nonnegative")
    observation, baseline, anomaly = _arrays(observation, baseline, anomaly)
    if sample_weight is None:
        importance = np.ones(observation.shape, dtype=np.float64)
    else:
        importance = np.broadcast_to(
            np.asarray(sample_weight, dtype=np.float64), observation.shape
        )
    valid = (
        np.isfinite(observation)
        & np.isfinite(baseline)
        & np.isfinite(anomaly)
        & np.isfinite(importance)
        & (importance > 0.0)
    )
    if not np.any(valid):
        return float(prior_weight)
    residual = observation[valid] - baseline[valid]
    term = anomaly[valid]
    importance = importance[valid]
    energy = float(np.sum(importance * term**2))
    mean_energy = energy / float(np.sum(importance))
    penalty = prior_equivalent_samples * mean_energy
    denominator = energy + penalty
    if denominator <= 0.0:
        return float(prior_weight)
    numerator = float(np.sum(importance * term * residual)) + penalty * prior_weight
    return float(np.clip(numerator / denominator, 0.0, 1.0))


def fit_vector_anomaly_weight(
    observed_u: ArrayLike,
    observed_v: ArrayLike,
    baseline_u: ArrayLike,
    baseline_v: ArrayLike,
    anomaly_u: ArrayLike,
    anomaly_v: ArrayLike,
    **kwargs,
) -> float:
    """Fit one shared weight for the two earth-relative wind components."""
    return fit_anomaly_weight(
        np.concatenate((np.ravel(observed_u), np.ravel(observed_v))),
        np.concatenate((np.ravel(baseline_u), np.ravel(baseline_v))),
        np.concatenate((np.ravel(anomaly_u), np.ravel(anomaly_v))),
        **kwargs,
    )


def _diagnostic(
    label: str,
    parent: str | None,
    selected: NDArray[np.bool_],
    observation: NDArray[np.float64],
    baseline: NDArray[np.float64],
    anomaly: NDArray[np.float64],
    weight: float,
    raw_weight: float,
    prior_weight: float,
) -> WeightFit:
    valid = selected & np.isfinite(observation) & np.isfinite(baseline) & np.isfinite(anomaly)
    return WeightFit(
        label=label,
        parent=parent,
        count=int(valid.sum()),
        weight=weight,
        unregularized_weight=raw_weight,
        prior_weight=prior_weight,
        baseline=continuous_metrics(observation[valid], baseline[valid]),
        hybrid=continuous_metrics(observation[valid], baseline[valid] + weight * anomaly[valid]),
    )


def fit_hierarchical_weights(
    observation: ArrayLike,
    baseline: ArrayLike,
    anomaly: ArrayLike,
    parent_labels: Iterable,
    child_labels: Iterable,
    *,
    parent_prior_samples: float = 500.0,
    child_prior_samples: float = 2000.0,
) -> list[WeightFit]:
    """Fit global, parent, then child weights with empirical-Bayes shrinkage."""
    observation, baseline, anomaly = _arrays(observation, baseline, anomaly)
    parents, children = np.broadcast_arrays(np.asarray(parent_labels), np.asarray(child_labels))
    parents = np.broadcast_to(parents, observation.shape)
    children = np.broadcast_to(children, observation.shape)
    all_rows = np.ones(observation.shape, dtype=bool)
    global_weight = fit_anomaly_weight(observation, baseline, anomaly)
    results = [
        _diagnostic(
            "global", None, all_rows, observation, baseline, anomaly,
            global_weight, global_weight, 0.0,
        )
    ]
    for parent_value in np.unique(parents):
        parent_rows = parents == parent_value
        parent_weight = fit_anomaly_weight(
            observation[parent_rows], baseline[parent_rows], anomaly[parent_rows],
            prior_weight=global_weight, prior_equivalent_samples=parent_prior_samples,
        )
        raw_parent = fit_anomaly_weight(
            observation[parent_rows], baseline[parent_rows], anomaly[parent_rows]
        )
        parent_name = str(parent_value)
        results.append(
            _diagnostic(
                parent_name, "global", parent_rows, observation, baseline, anomaly,
                parent_weight, raw_parent, global_weight,
            )
        )
        for child_value in np.unique(children[parent_rows]):
            child_rows = parent_rows & (children == child_value)
            child_weight = fit_anomaly_weight(
                observation[child_rows], baseline[child_rows], anomaly[child_rows],
                prior_weight=parent_weight, prior_equivalent_samples=child_prior_samples,
            )
            raw_child = fit_anomaly_weight(
                observation[child_rows], baseline[child_rows], anomaly[child_rows]
            )
            results.append(
                _diagnostic(
                    f"{parent_name}/{child_value}", parent_name, child_rows,
                    observation, baseline, anomaly, child_weight, raw_child, parent_weight,
                )
            )
    return results
