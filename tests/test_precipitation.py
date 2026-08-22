from __future__ import annotations

from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.precipitation import (
    PrecipitationQC,
    composite_precipitation,
    open_precipitation_candidate,
)


def test_compositor_exercises_preference_quality_override_and_fallback() -> None:
    candidates = {
        "mrms_pass2": np.array([[2.0, 2.0, np.nan, 2.0, np.nan]]),
        "mrms_pass1": np.array([[1.0, 1.0, 1.0, 1.0, np.nan]]),
        "stage4_archive": np.array([[3.0, 3.0, 3.0, np.nan, np.nan]]),
        "nldas2": np.array([[4.0, 4.0, 4.0, 4.0, np.nan]]),
        "hrrr": np.array([[5.0, 5.0, 5.0, 5.0, 5.0]]),
    }
    result = composite_precipitation(
        candidates,
        mrms_quality=np.array([[0.9, 0.9, 0.9, 0.1, np.nan]]),
        stage4_override=np.array([[False, True, False, False, False]]),
    )
    np.testing.assert_array_equal(result.source_id, [[1, 3, 2, 5, 6]])
    np.testing.assert_allclose(result.depth, [[2, 3, 1, 4, 5]])
    assert result.qc_flags[0, 1] & PrecipitationQC.STAGE4_OVERRIDE
    assert result.qc_flags[0, 3] & PrecipitationQC.MRMS_LOW_QUALITY
    assert result.qc_flags[0, 4] & PrecipitationQC.FALLBACK_USED


def test_compositor_leaves_missing_and_flags_extreme() -> None:
    result = composite_precipitation(
        {"hrrr": np.array([[301.0, np.nan]])}, extreme_depth=300.0
    )
    assert result.qc_flags[0, 0] & PrecipitationQC.EXTREME
    assert result.qc_flags[0, 1] & PrecipitationQC.MISSING
    assert np.isnan(result.depth[0, 1])


def test_mrms_adapter_masks_negative_no_coverage_and_sets_bounds(tmp_path: Path) -> None:
    path = tmp_path / "mrms.nc"
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("latitude", 1)
        data.createDimension("longitude", 2)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "seconds since 1970-01-01 00:00:00"
        time[:] = [3600]
        data.createVariable("latitude", "f8", ("latitude",))[:] = [40]
        data.createVariable("longitude", "f8", ("longitude",))[:] = [-100, -99]
        field = data.createVariable(
            "MultiSensorQPE01HPass2_0mabovemeansealevel",
            "f4",
            ("time", "latitude", "longitude"),
        )
        field.units = "mm"
        field[:] = [[[-3, 2]]]
    with open_precipitation_candidate(path, "mrms_pass2") as candidate:
        assert np.isnan(candidate.precipitation_depth[0, 0, 0])
        assert candidate.precipitation_depth[0, 0, 1] == 2
        assert (
            candidate.time_bounds[0, 1] - candidate.time_bounds[0, 0]
        ) == np.timedelta64(1, "h")
