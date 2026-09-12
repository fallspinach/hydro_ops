"""Guards for isolated northern fallback publication."""

import json
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset

from hydro_ops.forcing import gfs_publication
from hydro_ops.forcing.gfs_conservative import ConservativeGap
from hydro_ops.forcing.gfs_gap import sha
from hydro_ops.forcing.gfs_publication import (
    fill_active_holes,
    precipitation_supported,
    publish_gfs_day,
)


def test_preserve_supported_rain_only():
    ids = np.arange(9)
    expected = [False, True, True, True, True, True, False, True, False]
    assert precipitation_supported(ids, np.ones(9), np.zeros(9)).tolist() == expected
    assert not precipitation_supported(np.array([1, 2, 3]),
                                       np.array([np.nan, -1, 1]), np.array([0, 0, 8])).any()


def test_primary_holes_never_use_gfs_donors():
    values = np.array([[10., np.nan, 99.], [10., np.nan, 99.]])
    missing = ~np.isfinite(values)
    gap = np.array([[False, False, True], [False, False, True]])
    active = np.array([[True, True, True], [True, False, True]])
    keep = np.ones(values.shape, bool)
    result, repaired, distance = fill_active_holes(values, missing, active, keep, gap, {})
    assert result[0, 1] == 10
    assert np.isnan(result[1, 1])  # No new inactive-cell fills.
    assert np.all(result[:, 2] == 99)
    assert repaired.sum() == 1
    assert distance == 1


def test_nldas_precedence_writes_nothing(tmp_path):
    output = tmp_path / "output"
    report = publish_gfs_day(tmp_path / "input", output, None, None, None, None, None,
                             nldas_available=True, historical_test=True)
    assert report["status"] == "rebuild_with_nldas2"
    assert not output.exists()


def test_misleading_global_hrrr_label_cannot_override_nldas(tmp_path):
    source, output = tmp_path / "input", tmp_path / "output"
    with Dataset(source, "w") as data:
        data.forcing_source = "hrrr"
        data.createDimension("time", 24)
        data.createDimension("cell", 2)
        data.createVariable("forcing_source_id", "u1", ("time", "cell"))[:] = [0, 1]
    report = publish_gfs_day(source, output, None, None, None, None, None,
                            nldas_available=False, historical_test=True)
    assert report["status"] == "rebuild_with_nldas2"
    assert report["hourly_sources"] == ["nldas2"] * 24
    assert not output.exists()


@pytest.mark.parametrize("outage", [False, True])
def test_full_day_publication_preserves_input_and_supported_rain(tmp_path, monkeypatch, outage):
    keep = np.array([[True, True], [True, False]])
    lat = np.array([[50., 51.], [50., 51.]], dtype=np.float32)
    lon = np.array([[-110., -110.], [-109., -109.]], dtype=np.float32)
    envelope = tmp_path / "mask.nc"
    with Dataset(envelope, "w") as data:
        data.createDimension("y", 2)
        data.createDimension("x", 2)
        for name, values in (("keep", keep), ("active", keep), ("lat", lat), ("lon", lon)):
            data.createVariable(name, "f4", ("y", "x"))[:] = values
    geometry = tmp_path / "geometry.npz"
    np.savez(geometry, indices=np.array([1]), shape=np.array([2, 2]),
             terrain_fallback=np.array([False]), envelope_sha256=sha(keep),
             target_lat_sha256=sha(lat), target_lon_sha256=sha(lon))
    source, output = tmp_path / "input.nc", tmp_path / "output.nc"
    names = (*gfs_publication.MET_FIELDS, "RAINRATE")
    with Dataset(source, "w") as data:
        data.forcing_source = "hrrr"
        for name, length in (("time", 24), ("y", 2), ("x", 2)):
            data.createDimension(name, length)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "hours since 2026-08-24 00:00:00"
        time[:] = np.arange(24)
        for name, values in (("lat", lat), ("lon", lon)):
            data.createVariable(name, "f4", ("y", "x"))[:] = values
        for name in names:
            var = data.createVariable(name, "f4", ("time", "y", "x"), fill_value=-9999.)
            var[:] = [[10., -9999.], [-9999., 10.]]
        # Supported Stage-IV precipitation is preserved; HRRR rain is replaced.
        data["RAINRATE"][:, 0, 1] = 5.
        for name in ("forcing_source_id", "forcing_qc_flags", "precip_source_id", "precip_qc_flags", "precip_confidence"):
            data.createVariable(name, "f4", ("time", "y", "x"))[:] = 0
        data["forcing_source_id"][:, 0, 0] = 2
        data["precip_source_id"][:, 0, 1] = 3
        data["precip_source_id"][1, 0, 1] = 6
    before = source.read_bytes()

    class Downloader:
        def __init__(self, *args):
            pass

        def hour(self, valid, **kwargs):
            if outage and valid.hour == 2:
                raise RuntimeError("No complete GFS bundle")
            return SimpleNamespace(attrs={"cycle": (valid-timedelta(hours=1)).isoformat(), "lead": 1,
                "negative_roundoff_clipped": "{}", "url": "synthetic", "retrieved_utc": valid.isoformat()})

    monkeypatch.setattr(gfs_publication, "GfsDownloader", Downloader)
    monkeypatch.setattr(gfs_publication, "ConservativeGap", lambda *args: None)
    monkeypatch.setattr(gfs_publication, "remap_hour", lambda *args, **kwargs:
                        ({name: np.array([20.]) for name in names}, np.array([False])))
    if outage:
        with pytest.raises(RuntimeError, match="No complete GFS bundle"):
            publish_gfs_day(source, output, envelope, geometry, tmp_path / "weights", tmp_path,
                            tmp_path, nldas_available=False, historical_test=True)
        assert not output.exists()
        assert source.read_bytes() == before
        return
    report = publish_gfs_day(source, output, envelope, geometry, tmp_path / "weights", tmp_path,
                             tmp_path, nldas_available=False, historical_test=True)
    assert report["status"] == "passed"
    assert source.read_bytes() == before
    with Dataset(output) as data:
        assert np.all(data["T2D"][:, 0, 0] == 10)
        assert np.all(data["T2D"][:, 0, 1] == 20)
        assert np.all(data["T2D"][:, 1, 0] == 10)
        assert np.ma.getmaskarray(data["T2D"][:, 1, 1]).all()
        assert data["RAINRATE"][0, 0, 1] == 5
        assert data["RAINRATE"][1, 0, 1] == 20
        assert data["precip_source_id"][0, 0, 1] == 3
        assert data["precip_source_id"][1, 0, 1] == 8
    manifest = json.loads(output.with_name(output.name + ".manifest.json").read_text())
    assert manifest["verified"] and len(manifest["source_files"]) == 24


@pytest.mark.parametrize("coefficient,stale", [(1., False), (.5, False), (1., True)])
def test_conservative_identity_and_coverage(tmp_path, coefficient, stale):
    geometry = {"indices": np.array([3]), "source_lat": np.array([1.]),
                "source_lon": np.array([2.]), "envelope_sha256": "mask",
                "target_lat_sha256": "lat", "target_lon_sha256": "lon"}
    path = tmp_path / "weights.nc"
    with Dataset(path, "w") as data:
        data.createDimension("link", 1)
        data.createDimension("weight", 1)
        data.createVariable("src_address", "i4", ("link",))[:] = 1
        data.createVariable("dst_address", "i4", ("link",))[:] = 1
        data.createVariable("remap_matrix", "f8", ("link", "weight"))[:] = coefficient
        data.gfs_geometry_indices_sha256 = sha(geometry["indices"])
        data.gfs_source_lat_sha256 = sha(geometry["source_lat"])
        data.gfs_source_lon_sha256 = sha(geometry["source_lon"])
        data.gfs_envelope_sha256 = "stale" if stale else "mask"
        data.gfs_target_lat_sha256 = "lat"
        data.gfs_target_lon_sha256 = "lon"
    if coefficient != 1 or stale:
        with pytest.raises(ValueError):
            ConservativeGap(path, geometry)
    else:
        np.testing.assert_allclose(ConservativeGap(path, geometry)(np.array([[7.]])), [7.])
