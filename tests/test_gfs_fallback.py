from datetime import UTC, datetime

import numpy as np
import pytest

from hydro_ops.download.gfs import cycle_candidates, hourly_statistic, select
from hydro_ops.download.hrrr import parse_index
from hydro_ops.forcing.gfs_gap import MET_FIELDS, bilinear_weights, merge_gap


def test_cycle_boundary_and_fallback_cap():
    choices = list(cycle_candidates(datetime(2026, 1, 15, 0, tzinfo=UTC)))
    assert [(c.hour, h) for c, h in choices] == [(18, 6), (12, 12)]
    assert choices[0][0].day == 14
    with pytest.raises(ValueError):
        list(cycle_candidates(datetime(2026, 1, 15)))  # noqa: DTZ001 - test rejection


def test_statistics_and_reset():
    np.testing.assert_allclose(hourly_statistic([9], (0, 3), [5], (0, 2)), [4])
    np.testing.assert_allclose(hourly_statistic([120], (0, 3), [100], (0, 2), average=True), [160])
    np.testing.assert_allclose(hourly_statistic([5], (6, 7)), [5])
    with pytest.raises(ValueError, match="boundaries"):
        hourly_statistic([5], (6, 8), [4], (0, 7))


def test_bucket_selection_and_duplicates():
    records = parse_index("1:0:d=2026011500:APCP:surface:6-7 hour acc fcst:\n"
                          "2:100:d=2026011500:APCP:surface:0-7 hour acc fcst:\n"
                          "3:200:d=2026011500:TMP:2 m above ground:7 hour fcst:")
    record, window = select(records, "precipitation_depth", 7)
    assert record.number == 1 and window == (6, 7)
    assert select(records, "T2D", 7)[0].number == 3
    with pytest.raises(ValueError):
        select(records, "LWDOWN", 7)


def test_bilinear_no_extrapolation():
    addresses, weights = bilinear_weights(np.array([0, 1]), np.array([0, 1]), np.array([0.5]), np.array([0.25]))
    assert weights.sum() == 1
    assert (np.array([0, 1, 2, 3])[addresses] * weights).sum() == 1.25
    with pytest.raises(ValueError, match="extrapolation"):
        bilinear_weights(np.array([0, 1]), np.array([0, 1]), np.array([1.1]), np.array([0.2]))


def test_gap_only_merge_and_nldas_replacement():
    primary = {name: np.full((2, 2), 7.0) for name in (*MET_FIELDS, "RAINRATE")}
    fallback = {name: np.array([10., 11.]) for name in primary}
    indices = np.array([1, 3])
    precip_supported = np.array([[True, True], [True, False]])
    out, used, rain_used = merge_gap(primary, fallback, indices, precip_supported, nldas_available=False)
    np.testing.assert_array_equal(out["T2D"], [[7, 10], [7, 11]])
    np.testing.assert_array_equal(out["RAINRATE"], [[7, 7], [7, 11]])
    assert used.sum() == 2 and rain_used.sum() == 1
    out, used, rain_used = merge_gap(primary, fallback, indices, precip_supported, nldas_available=True)
    assert not used.any() and not rain_used.any()
    np.testing.assert_array_equal(out["T2D"], primary["T2D"])


def test_available_cycle_fallback_keeps_bundle(tmp_path, monkeypatch):
    import json

    import xarray as xr

    from hydro_ops.download.gfs import GfsDownloader

    downloader = GfsDownloader(tmp_path, tmp_path)
    def fake(cycle, lead):
        if cycle.hour == 0:
            raise ValueError("newest bundle incomplete")
        assert cycle.hour == 18 and lead == 7
        result = xr.Dataset({name: (("lat", "lon"), np.array([[1.]]))
                             for name in ("precipitation_depth", "LWDOWN", "SWDOWN")},
                            coords={"lat": [50.], "lon": [-100.]})
        result.attrs.update(cycle=cycle.isoformat(), lead=lead,
                            windows=json.dumps({name: [6, 7] for name in result.data_vars}))
        return result
    monkeypatch.setattr(downloader, "forecast", fake)
    hour = downloader.hour(datetime(2026, 1, 15, 1, tzinfo=UTC))
    assert hour.attrs["lead"] == 7


def test_asof_rejects_future_retrieval_without_publication_evidence(tmp_path, monkeypatch):
    import json

    import xarray as xr

    from hydro_ops.download.gfs import GfsDownloader

    downloader = GfsDownloader(tmp_path, tmp_path)
    def fake(cycle, lead):
        return xr.Dataset(attrs={"cycle": cycle.isoformat(), "lead": lead,
            "windows": json.dumps({}), "retrieved_utc": "2026-09-12T00:00:00+00:00"})
    monkeypatch.setattr(downloader, "forecast", fake)
    with pytest.raises(RuntimeError, match="available"):
        downloader.hour(datetime(2026, 1, 15, 1, tzinfo=UTC), as_of=datetime(2026, 1, 15, 2, tzinfo=UTC))


def test_cached_lead_is_json_serializable(tmp_path):
    import json

    import xarray as xr

    from hydro_ops.download.gfs import GfsDownloader

    cycle = datetime(2026, 1, 15, tzinfo=UTC)
    path = tmp_path / "2026011500/f001.nc"
    path.parent.mkdir()
    xr.Dataset(attrs={"cycle": cycle.isoformat(), "lead": 1}).to_netcdf(path)
    result = GfsDownloader(tmp_path, tmp_path).forecast(cycle, 1)
    assert json.loads(json.dumps(result.attrs))["lead"] == 1
