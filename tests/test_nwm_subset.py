import numpy as np
import pytest

from hydro_ops.nwm_subset import GridWindow, window_from_bbox


def test_window_from_bbox_with_padding_and_refinement() -> None:
    longitude, latitude = np.meshgrid(np.arange(-5.0, 1.0), np.arange(40.0, 45.0))
    window = window_from_bbox(latitude, longitude, (-3.1, 40.9, -1.9, 43.1), padding=1)

    assert window == GridWindow(1, 4, 0, 4)
    assert window.shape == (5, 4)
    assert window.refined(4) == GridWindow(4, 19, 0, 19)


def test_window_rejects_bbox_outside_grid() -> None:
    longitude, latitude = np.meshgrid(np.arange(3.0), np.arange(2.0))
    with pytest.raises(ValueError, match="does not intersect"):
        window_from_bbox(latitude, longitude, (10.0, 10.0, 11.0, 11.0))
