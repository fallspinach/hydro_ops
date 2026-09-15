"""Safety checks for the isolated static-mask pilot utility."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

SPEC = importlib.util.spec_from_file_location("static_mask_pilot", Path(__file__).parents[1] / "bin/test_static_forcing_mask.py")
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def fixture_files(tmp_path, chunksizes=None):
    source, mask = tmp_path / "source.nc", tmp_path / "mask.nc"
    keep = np.array([[True, True], [False, False]])
    active = np.array([[True, False], [False, False]])
    for path in (source, mask):
        with Dataset(path, "w") as data:
            data.createDimension("y", 2)
            data.createDimension("x", 2)
            data.createVariable("lat", "f8", ("y", "x"))[:] = 40
            data.createVariable("lon", "f8", ("y", "x"))[:] = -100
            if path == mask:
                data.createVariable("keep", "u1", ("y", "x"))[:] = keep
                data.createVariable("active", "u1", ("y", "x"))[:] = active
                data.keep_sha256, data.active_sha256 = pilot.digest(keep), pilot.digest(active)
            else:
                data.forcing_source = "nldas2"
                data.createDimension("time", 2)
                data.createVariable("time", "i4", ("time",))[:] = [0, 1]
                for name in pilot.FIELDS:
                    var = data.createVariable(name, "f4", ("time", "y", "x"), fill_value=-9999,
                                              zlib=True, complevel=2, chunksizes=chunksizes)
                    var[:] = [[[1, -9999], [3, 4]], [[5, 6], [7, 8]]]
    return source, mask


def test_clip_copy_preserves_values_and_holes(tmp_path):
    source, mask = fixture_files(tmp_path)
    before = source.read_bytes()
    result = pilot.run(source, mask, tmp_path / "output.nc", tmp_path / "work")
    assert result["status"] == "passed"
    assert source.read_bytes() == before
    for stats in result["fields"].values():
        assert stats["removed_valid_cell_hours"] == 4
        assert stats["missing_retained_cell_hours"] == 1
        assert stats["active_unchanged"] and stats["retained_unchanged"]
    with Dataset(tmp_path / "output.nc") as data:
        assert data["T2D"].filters()["complevel"] == 2
        assert np.all(np.ma.getmaskarray(data["T2D"][:])[:, 1, :])


def test_missing_active_refuses_publication(tmp_path):
    source, mask = fixture_files(tmp_path)
    with Dataset(source, "r+") as data:
        data["T2D"][1, 0, 0] = -9999
    output = tmp_path / "output.nc"
    with pytest.raises(ValueError, match="missing active"):
        pilot.run(source, mask, output, tmp_path / "work")
    assert not output.exists()


def test_in_place_refused(tmp_path):
    source, mask = fixture_files(tmp_path)
    with pytest.raises(ValueError, match="separate file"):
        pilot.run(source, mask, source, tmp_path / "work")


def test_mask_excluding_active_refused(tmp_path):
    source, mask = fixture_files(tmp_path)
    with Dataset(mask, "r+") as data:
        data["keep"][0, 0] = 0
        data.keep_sha256 = pilot.digest(np.asarray(data["keep"][:], bool))
    with pytest.raises(ValueError, match="Unsafe static mask"):
        pilot.run(source, mask, tmp_path / "output.nc", tmp_path / "work")


def test_grid_mismatch_refused(tmp_path):
    source, mask = fixture_files(tmp_path)
    with Dataset(source, "r+") as data:
        data["lat"][0, 0] = 41
    with pytest.raises(AssertionError):
        pilot.run(source, mask, tmp_path / "output.nc", tmp_path / "work")


@pytest.mark.parametrize('fast', [False, True])
def test_production_publish_resume_and_manifest_recovery(tmp_path, monkeypatch, fast):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "bin"))
    monkeypatch.setitem(sys.modules, "test_static_forcing_mask", pilot)
    import apply_static_forcing_mask as production

    original, mask = fixture_files(tmp_path, chunksizes=(1, 1, 1))
    root = tmp_path / "retro"
    source = root / "1979/05/19790515.LDASIN_DOMAIN1"
    source.parent.mkdir(parents=True)
    original.rename(source)
    manifest = source.with_name(source.name + ".manifest.json")
    manifest.write_text(json.dumps({"verified": True, "source_provenance": "preserve-me"}))
    with Dataset(mask, "r+") as data:
        data.policy = production.POLICY
        data.status = "operational_historical_archive"
    state = tmp_path / "state"
    args = (source, mask, root, state, tmp_path / "work")
    assert production.publish(*args, fast=fast)["status"] == "published"
    assert production.publish(*args)["status"] == "already_verified"
    assert json.loads(manifest.read_text())["source_provenance"] == "preserve-me"
    journal = state / "19790515.json"
    report = json.loads(journal.read_text())
    if fast:
        assert report['full_readback']  # Unknown input audit must fall back.
    report["status"] = "ready_to_publish"
    journal.write_text(json.dumps(report))
    manifest.unlink()
    assert production.publish(*args)["status"] == "published"
    assert json.loads(manifest.read_text())["source_provenance"] == "preserve-me"
    with pytest.raises(ValueError):
        production.publish(source, mask, tmp_path / "different-root", state, tmp_path / "work")


def test_post2020_clipping_requires_explicit_owned_staging(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "bin"))
    monkeypatch.setitem(sys.modules, "test_static_forcing_mask", pilot)
    import apply_static_forcing_mask as production

    original, mask = fixture_files(tmp_path)
    work = tmp_path / "scratch"
    root = work / "candidate/retro"
    source = root / "2020/12/20201213.LDASIN_DOMAIN1"
    source.parent.mkdir(parents=True)
    original.rename(source)
    with Dataset(mask, "r+") as data:
        data.policy, data.status = production.POLICY, "operational_historical_archive"
    args = (source, mask, root, tmp_path / "state", work)
    with pytest.raises(ValueError, match="authorized"):
        production.publish(*args)
    with pytest.raises(ValueError, match="provenance"):
        production.publish(*args, staged_rebuild=True)
    with Dataset(source, "r+") as data:
        data.archive_granularity = "utc_calendar_day"
        data.prism_reconciliation_accepted = "true"
        data.cnrfc_stage4_policy = "hourly Stage-IV rejected"
    with pytest.raises(ValueError):
        production.publish(source, mask, root, tmp_path / "state", tmp_path / "other",
                           staged_rebuild=True)
    assert production.publish(*args, staged_rebuild=True)["status"] == "published"
    with Dataset(source) as data:
        assert data.forcing_domain_policy == production.POLICY
        assert data.forcing_domain_content_audit == production.AUDIT
        np.testing.assert_array_equal(data["T2D"][:, 0, 0], [1, 5])
        assert np.ma.getmaskarray(data["T2D"][:])[:, 1, :].all()


def test_new_campaign_staging_and_final_transfer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "bin"))
    monkeypatch.setitem(sys.modules, "test_static_forcing_mask", pilot)
    import apply_static_forcing_mask as production

    original, mask = fixture_files(tmp_path)
    work = tmp_path / "scratch"
    root = work / "calendar/retro"
    source = root / "2003/07/20030701.LDASIN_DOMAIN1"
    source.parent.mkdir(parents=True)
    original.rename(source)
    with Dataset(mask, "r+") as data:
        data.policy, data.status = production.POLICY, "operational_historical_archive"
    with Dataset(source, "r+") as data:
        data.archive_granularity = "utc_calendar_day"
        data.prism_reconciliation_accepted = "true"
    state = tmp_path / "state"
    args = (source, mask, root, state, work)
    with pytest.raises(ValueError, match="authorized"):
        production.publish(*args)
    with pytest.raises(ValueError):
        production.publish(source, mask, root, state, tmp_path / "other", staged_production=True)
    assert production.publish(*args, staged_production=True)["status"] == "published"
    final = tmp_path / "permanent/2003/07" / source.name
    production.transfer_publication(source, final, state)
    manifest = json.loads(final.with_name(final.name + ".manifest.json").read_text())
    assert manifest["daily_file"] == str(final)
    assert manifest["static_envelope"]["published_identity"] == production.identity(final)
    assert production.file_hash(final) == production.file_hash(source)
