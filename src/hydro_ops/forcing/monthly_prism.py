"""Low-memory monthly PRISM constraint primitives for the 1979-1980 tier."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import distance_transform_edt

from hydro_ops.forcing.precipitation_reconciliation import (
    ConservativeOperator,
    ReconciliationQC,
    ReconciliationResult,
    reconcile_prism_period,
)


@dataclass(frozen=True)
class MonthlyTemperatureAdjustment:
    """Spatial affine coefficients derived from mean daily extrema."""

    baseline_midpoint: NDArray[np.float64]
    target_midpoint: NDArray[np.float64]
    range_scale: NDArray[np.float64]
    midpoint_shift: NDArray[np.float64]
    constraint_valid: NDArray[np.bool_]
    used_midpoint_only: NDArray[np.bool_]
    scale_was_clipped: NDArray[np.bool_]

    def apply(self, temperature: np.ndarray) -> NDArray[np.float64]:
        """Apply the monthly coefficients without changing submonthly ordering."""
        values = np.asarray(temperature, dtype=np.float64)
        corrected = self.target_midpoint + self.range_scale * (values - self.baseline_midpoint)
        return np.where(self.constraint_valid, corrected, values)


@dataclass(frozen=True)
class MonthlyReconciliationAcceptance:
    """Publication-gate metrics for a monthly precipitation solve."""

    accepted: bool
    constrained_cells: int
    unconverged_fraction: float
    unresolved_fraction: float
    capped_fraction: float
    dry_baseline_wet_target_fraction: float
    synthetic_timing_fraction: float


def assess_monthly_reconciliation(
    result: ReconciliationResult,
    prism_depth: np.ndarray,
    *,
    tolerance: float = 1.0e-3,
    maximum_unconverged_fraction: float = 0.005,
    maximum_unresolved_fraction: float = 0.005,
    maximum_capped_fraction: float = 0.02,
    maximum_dry_baseline_wet_target_fraction: float = 0.005,
    maximum_synthetic_timing_fraction: float = 0.01,
) -> MonthlyReconciliationAcceptance:
    """Apply independent convergence and physical-safeguard publication gates."""
    flags = np.asarray(result.target_qc_flags, dtype=np.uint16)
    target = np.asarray(prism_depth, dtype=np.float64)
    residual = np.asarray(result.target_residual, dtype=np.float64)
    if flags.shape != target.shape or residual.shape != target.shape:
        raise ValueError("Monthly target, residual, and QC grids must have identical shapes")
    constrained = np.isfinite(target) & ((flags & np.uint16(ReconciliationQC.PRISM_MISSING)) == 0)
    count = max(np.count_nonzero(constrained), 1)
    unconverged = constrained & ((flags & np.uint16(ReconciliationQC.NOT_CONVERGED)) != 0)
    capped = constrained & ((flags & np.uint16(ReconciliationQC.RATIO_CAPPED)) != 0)
    dry_wet = constrained & ((flags & np.uint16(ReconciliationQC.BASE_DRY_TARGET_WET)) != 0)
    synthetic = constrained & ((flags & np.uint16(ReconciliationQC.SYNTHETIC_TIMING)) != 0)
    unhandled_dry_wet = dry_wet & ~synthetic
    relative_error = np.divide(
        np.abs(residual),
        np.maximum(target, 1.0),
        out=np.full_like(residual, np.inf),
        where=constrained & np.isfinite(residual),
    )
    unresolved = constrained & (relative_error > tolerance)
    fractions = tuple(
        float(np.count_nonzero(mask) / count)
        for mask in (unconverged, unresolved, capped, unhandled_dry_wet, synthetic)
    )
    limits = (
        maximum_unconverged_fraction,
        maximum_unresolved_fraction,
        maximum_capped_fraction,
        maximum_dry_baseline_wet_target_fraction,
        maximum_synthetic_timing_fraction,
    )
    if any(value < 0 or value > 1 for value in limits):
        raise ValueError("Monthly acceptance fractions must be between zero and one")
    return MonthlyReconciliationAcceptance(
        accepted=all(value <= limit for value, limit in zip(fractions, limits, strict=True)),
        constrained_cells=int(np.count_nonzero(constrained)),
        unconverged_fraction=fractions[0],
        unresolved_fraction=fractions[1],
        capped_fraction=fractions[2],
        dry_baseline_wet_target_fraction=fractions[3],
        synthetic_timing_fraction=fractions[4],
    )


def monthly_temperature_adjustment(
    mean_daily_minimum: np.ndarray,
    mean_daily_maximum: np.ndarray,
    prism_minimum: np.ndarray,
    prism_maximum: np.ndarray,
    *,
    minimum_baseline_range: float = 0.5,
    scale_bounds: tuple[float, float] = (0.25, 4.0),
) -> MonthlyTemperatureAdjustment:
    """Derive an affine correction for monthly means of daily Tmin/Tmax."""
    baseline_minimum = np.asarray(mean_daily_minimum, dtype=np.float64)
    baseline_maximum = np.asarray(mean_daily_maximum, dtype=np.float64)
    target_minimum = np.asarray(prism_minimum, dtype=np.float64)
    target_maximum = np.asarray(prism_maximum, dtype=np.float64)
    if not (
        baseline_minimum.shape
        == baseline_maximum.shape
        == target_minimum.shape
        == target_maximum.shape
    ):
        raise ValueError("Monthly temperature fields must have identical shapes")
    valid = (
        np.isfinite(target_minimum)
        & np.isfinite(target_maximum)
        & (target_maximum >= target_minimum)
        & np.isfinite(baseline_minimum)
        & np.isfinite(baseline_maximum)
        & (baseline_maximum >= baseline_minimum)
    )
    baseline_midpoint = (baseline_minimum + baseline_maximum) / 2.0
    target_midpoint = np.where(valid, (target_minimum + target_maximum) / 2.0, baseline_midpoint)
    baseline_range = baseline_maximum - baseline_minimum
    target_range = target_maximum - target_minimum
    midpoint_only = valid & (baseline_range < minimum_baseline_range)
    raw_scale = np.divide(
        target_range,
        baseline_range,
        out=np.ones_like(baseline_range),
        where=valid & ~midpoint_only,
    )
    lower, upper = scale_bounds
    if lower <= 0 or upper < lower:
        raise ValueError("Invalid scale bounds")
    scale = np.where(valid, np.clip(raw_scale, lower, upper), 1.0)
    clipped = valid & ~midpoint_only & (scale != raw_scale)
    return MonthlyTemperatureAdjustment(
        baseline_midpoint=baseline_midpoint,
        target_midpoint=target_midpoint,
        range_scale=scale,
        midpoint_shift=target_midpoint - baseline_midpoint,
        constraint_valid=valid,
        used_midpoint_only=midpoint_only,
        scale_was_clipped=clipped,
    )


def reconcile_prism_month(
    monthly_depth: np.ndarray,
    prism_depth: np.ndarray,
    operator: ConservativeOperator,
    *,
    tolerance: float = 1.0e-3,
    max_iterations: int = 80,
    ratio_bounds: tuple[float, float] = (0.1, 10.0),
    cumulative_ratio_bounds: tuple[float, float] = (0.0, 10.0),
    maximum_monthly_depth: float = 4000.0,
    damping: float = 1.0,
    allow_synthetic_timing: bool = False,
) -> ReconciliationResult:
    """Derive a monthly precipitation factor from an accumulated NWM field.

    Only the accumulated field enters the solve. The caller applies the returned
    correction factor to every hourly depth, preserving the baseline timing exactly.
    """
    accumulated = np.asarray(monthly_depth, dtype=np.float64)
    return reconcile_prism_period(
        accumulated[None, ...],
        prism_depth,
        operator,
        tolerance=tolerance,
        max_iterations=max_iterations,
        ratio_bounds=ratio_bounds,
        cumulative_ratio_bounds=cumulative_ratio_bounds,
        maximum_period_depth=maximum_monthly_depth,
        damping=damping,
        allow_synthetic_timing=allow_synthetic_timing,
    )


def nearest_wet_timing_donors(
    baseline_monthly_depth: np.ndarray,
    corrected_monthly_depth: np.ndarray,
    *,
    dry_tolerance: float = 1.0e-8,
) -> tuple[NDArray[np.bool_], NDArray[np.intp], NDArray[np.intp]]:
    """Locate the nearest wet NWM cell for each synthesized monthly source cell."""
    baseline = np.asarray(baseline_monthly_depth, dtype=np.float64)
    corrected = np.asarray(corrected_monthly_depth, dtype=np.float64)
    if baseline.shape != corrected.shape or baseline.ndim != 2:
        raise ValueError("Monthly precipitation grids must be matching two-dimensional arrays")
    wet = np.isfinite(baseline) & (baseline > dry_tolerance)
    synthetic = (
        np.isfinite(baseline)
        & (baseline <= dry_tolerance)
        & np.isfinite(corrected)
        & (corrected > dry_tolerance)
    )
    if np.any(synthetic) and not np.any(wet):
        raise ValueError("Synthetic monthly precipitation has no wet timing donor")
    indices = distance_transform_edt(~wet, return_distances=False, return_indices=True)
    return synthetic, indices[0], indices[1]


def apply_monthly_precipitation_hour(
    rainrate: np.ndarray,
    baseline_monthly_depth: np.ndarray,
    corrected_monthly_depth: np.ndarray,
    correction_factor: np.ndarray,
    synthetic_mask: np.ndarray,
    donor_y: np.ndarray,
    donor_x: np.ndarray,
) -> NDArray[np.float64]:
    """Apply ordinary factors and nearest-wet temporal profiles to one hourly field."""
    rain = np.asarray(rainrate, dtype=np.float64)
    factor = np.asarray(correction_factor, dtype=np.float64)
    result = rain * np.where(np.isfinite(factor), factor, 1.0)
    if np.any(synthetic_mask):
        donor_depth = baseline_monthly_depth[donor_y, donor_x]
        donor_rate = rain[donor_y, donor_x]
        scale = np.divide(
            corrected_monthly_depth,
            donor_depth,
            out=np.zeros_like(corrected_monthly_depth, dtype=np.float64),
            where=np.isfinite(donor_depth) & (donor_depth > 0),
        )
        result[synthetic_mask] = (donor_rate * scale)[synthetic_mask]
    return result
