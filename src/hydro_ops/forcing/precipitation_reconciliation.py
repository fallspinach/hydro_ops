"""Conservative PRISM daily precipitation reconciliation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from numpy.typing import NDArray


class ReconciliationQC(IntFlag):
    """Daily target-cell reconciliation diagnostics."""

    PRISM_MISSING = 1
    TARGET_DRY = 2
    BASE_DRY_TARGET_WET = 4
    SYNTHETIC_TIMING = 8
    RATIO_CAPPED = 16
    NOT_CONVERGED = 32


def build_nwm_to_prism_weight_command(
    executable: str, nwm_grid: Path, prism_grid: Path, output: Path
) -> list[str]:
    """Build the CDO command for the reverse operator required by reconciliation."""
    return [
        executable,
        "-O",
        f"gencon,{prism_grid}",
        "-selname,active_domain",
        str(nwm_grid),
        str(output),
    ]


@dataclass(frozen=True)
class ConservativeOperator:
    """A compact one-based-CDO-compatible sparse conservative operator."""

    source_size: int
    target_size: int
    source_index: NDArray[np.int64]
    target_index: NDArray[np.int64]
    weight: NDArray[np.float64]

    @classmethod
    def from_cdo(cls, path: Path) -> ConservativeOperator:
        """Read the first weight from each CDO/SCRIP link into zero-based arrays."""
        with Dataset(path) as data:
            source = np.asarray(data["src_address"][:], dtype=np.int64).reshape(-1) - 1
            target = np.asarray(data["dst_address"][:], dtype=np.int64).reshape(-1) - 1
            matrix = np.asarray(data["remap_matrix"][:], dtype=np.float64)
            weight = matrix.reshape(matrix.shape[0], -1)[:, 0]
            source_size = int(np.prod(np.asarray(data["src_grid_dims"][:], dtype=int)))
            target_size = int(np.prod(np.asarray(data["dst_grid_dims"][:], dtype=int)))
        if source.size != target.size or source.size != weight.size:
            raise ValueError(f"Malformed conservative weights: {path}")
        valid = (
            (source >= 0)
            & (source < source_size)
            & (target >= 0)
            & (target < target_size)
            & np.isfinite(weight)
            & (weight > 0)
        )
        return cls(source_size, target_size, source[valid], target[valid], weight[valid])

    def apply(self, values: np.ndarray) -> NDArray[np.float64]:
        """Apply the sparse source-to-target operator, ignoring nonfinite links."""
        source = np.asarray(values, dtype=np.float64).reshape(-1)
        if source.size != self.source_size:
            raise ValueError(f"Expected {self.source_size} source cells, got {source.size}")
        linked = source[self.source_index]
        valid = np.isfinite(linked)
        numerator = np.bincount(
            self.target_index[valid],
            weights=self.weight[valid] * linked[valid],
            minlength=self.target_size,
        )
        denominator = np.bincount(
            self.target_index[valid], weights=self.weight[valid], minlength=self.target_size
        )
        return np.divide(
            numerator,
            denominator,
            out=np.full(self.target_size, np.nan),
            where=denominator > 0,
        )

    def backproject_ratio(
        self, ratio: np.ndarray, *, unmapped_value: float = 1.0
    ) -> NDArray[np.float64]:
        """Return the link-weighted target ratio at every source cell."""
        target = np.asarray(ratio, dtype=np.float64).reshape(-1)
        if target.size != self.target_size:
            raise ValueError(f"Expected {self.target_size} target cells, got {target.size}")
        linked = target[self.target_index]
        valid = np.isfinite(linked)
        numerator = np.bincount(
            self.source_index[valid],
            weights=self.weight[valid] * linked[valid],
            minlength=self.source_size,
        )
        denominator = np.bincount(
            self.source_index[valid], weights=self.weight[valid], minlength=self.source_size
        )
        return np.divide(
            numerator,
            denominator,
            out=np.full(self.source_size, unmapped_value, dtype=np.float64),
            where=denominator > 0,
        )


@dataclass(frozen=True)
class ReconciliationResult:
    hourly_depth: NDArray[np.float64]
    daily_depth: NDArray[np.float64]
    correction_factor: NDArray[np.float64]
    target_residual: NDArray[np.float64]
    target_qc_flags: NDArray[np.uint16]
    iterations: int
    converged: bool


def reconcile_prism_day(
    hourly_depth: np.ndarray,
    prism_depth: np.ndarray,
    operator: ConservativeOperator,
    *,
    tolerance: float = 1.0e-3,
    max_iterations: int = 100,
    ratio_bounds: tuple[float, float] = (0.1, 10.0),
    dry_tolerance: float = 1.0e-8,
) -> ReconciliationResult:
    """Reconcile 24 NWM hours to PRISM using bounded multiplicative projections.

    Missing PRISM cells remain unconstrained. When the baseline is dry but PRISM is wet,
    a conservative backprojection seeds the daily field and the domain wet-hour profile is
    used; those cells are explicitly marked as synthetic timing.
    """
    hourly = np.asarray(hourly_depth, dtype=np.float64)
    original_shape = hourly.shape
    if hourly.ndim < 2 or hourly.shape[0] != 24:
        raise ValueError("A PRISM day requires exactly 24 hourly fields")
    flat = hourly.reshape(24, -1)
    if flat.shape[1] != operator.source_size:
        raise ValueError("Hourly grid and conservative operator source grid differ")
    if np.any(flat[np.isfinite(flat)] < 0):
        raise ValueError("Hourly precipitation must be nonnegative")
    prism = np.asarray(prism_depth, dtype=np.float64).reshape(-1)
    if prism.size != operator.target_size:
        raise ValueError("PRISM grid and conservative operator target grid differ")
    if np.any(prism[np.isfinite(prism)] < 0):
        raise ValueError("PRISM precipitation must be nonnegative")

    base = np.nansum(flat, axis=0)
    base[~np.any(np.isfinite(flat), axis=0)] = np.nan
    corrected = np.nan_to_num(base, nan=0.0)
    target_qc = np.zeros(operator.target_size, dtype=np.uint16)
    constrained = np.isfinite(prism)
    target_qc[~constrained] |= np.uint16(ReconciliationQC.PRISM_MISSING)
    target_qc[constrained & (prism <= dry_tolerance)] |= np.uint16(ReconciliationQC.TARGET_DRY)

    initial_target = operator.apply(corrected)
    wet_dry = constrained & (prism > dry_tolerance) & (
        ~np.isfinite(initial_target) | (initial_target <= dry_tolerance)
    )
    if np.any(wet_dry):
        target_qc[wet_dry] |= np.uint16(
            ReconciliationQC.BASE_DRY_TARGET_WET | ReconciliationQC.SYNTHETIC_TIMING
        )
        seed_target = np.where(wet_dry, prism, np.nan)
        seed = operator.backproject_ratio(seed_target, unmapped_value=0.0)
        corrected = np.where((corrected <= dry_tolerance) & (seed > 0), seed, corrected)

    lower, upper = ratio_bounds
    if not (0 < lower <= 1 <= upper):
        raise ValueError("ratio_bounds must bracket one and be positive")
    converged = False
    residual = np.full(operator.target_size, np.nan)
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        estimate = operator.apply(corrected)
        ratio = np.ones(operator.target_size)
        wet = constrained & (prism > dry_tolerance) & (estimate > dry_tolerance)
        ratio[wet] = prism[wet] / estimate[wet]
        ratio[constrained & (prism <= dry_tolerance)] = 0.0
        capped = wet & ((ratio < lower) | (ratio > upper))
        target_qc[capped] |= np.uint16(ReconciliationQC.RATIO_CAPPED)
        ratio[wet] = np.clip(ratio[wet], lower, upper)
        corrected *= operator.backproject_ratio(np.where(constrained, ratio, 1.0))
        estimate = operator.apply(corrected)
        residual[constrained] = estimate[constrained] - prism[constrained]
        scale = np.maximum(prism[constrained], 1.0)
        if not constrained.any() or np.nanmax(np.abs(residual[constrained]) / scale) <= tolerance:
            converged = True
            break
    if not converged:
        target_qc[constrained] |= np.uint16(ReconciliationQC.NOT_CONVERGED)

    fractions = np.divide(
        np.nan_to_num(flat, nan=0.0),
        base,
        out=np.zeros_like(flat),
        where=np.isfinite(base) & (base > dry_tolerance),
    )
    wet_hour_profile = np.nansum(np.nan_to_num(flat, nan=0.0), axis=1)
    if wet_hour_profile.sum() > 0:
        wet_hour_profile /= wet_hour_profile.sum()
    else:
        wet_hour_profile[:] = 1 / 24
    synthetic_source = (base <= dry_tolerance) & (corrected > dry_tolerance)
    fractions[:, synthetic_source] = wet_hour_profile[:, None]
    corrected_hourly = fractions * corrected[None, :]
    factor = np.divide(
        corrected,
        base,
        out=np.full(operator.source_size, np.nan),
        where=np.isfinite(base) & (base > dry_tolerance),
    )
    return ReconciliationResult(
        corrected_hourly.reshape(original_shape),
        corrected.reshape(original_shape[1:]),
        factor.reshape(original_shape[1:]),
        residual.reshape(np.asarray(prism_depth).shape),
        target_qc.reshape(np.asarray(prism_depth).shape),
        iterations,
        converged,
    )
