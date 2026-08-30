import numpy as np
import pytest

from hydro_ops.forcing.coverage import fill_persistent_gaps, persistent_gap_mask


def test_persistent_gap_mask_excludes_transient_missing() -> None:
    missing = np.zeros((2, 3, 2, 3), dtype=bool)
    missing[0, :, 0, 0] = True
    missing[1, 1, 1, 1] = True
    result = persistent_gap_mask(missing)
    assert result[0, 0]
    assert not result[1, 1]


def test_fill_persistent_gap_records_distance_without_changing_fringe() -> None:
    values = np.array([[1.0, -999.0, -999.0, -999.0]])
    missing = values == -999.0
    land = np.array([[False, False, True, False]])
    allowed = np.array([[False, False, True, False]])
    result = fill_persistent_gaps(
        values, missing=missing, active_land=land, allowed=allowed, max_distance=2
    )
    assert result.values.tolist() == [[1.0, -999.0, 1.0, -999.0]]
    assert result.distance[0, 2] == 2


def test_fill_rejects_transient_and_distant_gaps() -> None:
    values = np.array([[1.0, -999.0, -999.0]])
    missing = values == -999.0
    land = np.array([[False, False, True]])
    with pytest.raises(ValueError, match="unapproved"):
        fill_persistent_gaps(
            values,
            missing=missing,
            active_land=land,
            allowed=np.zeros_like(land),
            max_distance=2,
        )
    with pytest.raises(ValueError, match="exceed"):
        fill_persistent_gaps(
            values, missing=missing, active_land=land, allowed=land, max_distance=1
        )
