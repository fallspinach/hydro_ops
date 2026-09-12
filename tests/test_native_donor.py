import numpy as np
import pytest

from hydro_ops.forcing.native_donor import nearest_native, repair_arrays
from hydro_ops.forcing.source_provenance import classify_hour


def test_isolated_native_donor_and_distance_limit():
    lat = np.array([[29., 29.], [29.125, 29.125]])
    lon = np.array([[-118.25, -118.125], [-118.25, -118.125]])
    valid = np.array([[True, False], [False, False]])
    indices, distances = nearest_native(lat, lon, valid, np.array([29.01]), np.array([-118.24]))
    assert indices.tolist() == [0] and distances[0] < 2
    with pytest.raises(ValueError, match="exceeds"):
        nearest_native(lat, lon, valid, np.array([30.]), np.array([-115.]))


def test_coupled_native_repair_preserves_rain_and_good_cells():
    values = {"T2D": 280., "PSFC": 100000., "Q2D": .004, "LWDOWN": 300.,
              "U2D": 2., "V2D": -1., "SWDOWN": 200., "RAINRATE": .001}
    native = {n: np.array([[v, np.nan]]) for n, v in values.items()}
    primary = {n: np.array([[v, np.nan]]) for n, v in values.items()}
    primary["RAINRATE"][0, 1] = .02
    before = {n: v.copy() for n, v in primary.items()}
    lat = np.array([[29., 29.01]])
    lon = np.array([[-118.25, -118.24]])
    result, flags, distances, report = repair_arrays(primary, native, np.zeros((1, 2)), lat, lon,
        np.array([[0., 100.]]), lat, lon, np.ones((1, 2), bool))
    for n, value in primary.items():
        np.testing.assert_equal(value, before[n])
        assert result[n][0, 0] == before[n][0, 0]
        assert np.isfinite(result[n]).all()
    assert result["T2D"][0, 1] < 280
    assert result["PSFC"][0, 1] < 100000
    assert result["RAINRATE"][0, 1] == .02
    assert flags[0, 1] & 1 and not flags[0, 1] & 8
    assert distances.max() < 2 and report["T2D"]["cells"] == 1


@pytest.mark.parametrize("ids,expected", [([0, 1], "nldas2"), ([0, 2], "hrrr"),
                                         ([1, 2], "mixed"), ([0, 3], "nldas2_hrrr_hybrid")])
def test_hourly_provenance(ids, expected):
    assert classify_hour(np.array(ids)) == expected


def test_empty_provenance_rejected():
    with pytest.raises(ValueError):
        classify_hour(np.array([0, 0]))
