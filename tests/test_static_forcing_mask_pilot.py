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


def fixture_files(tmp_path):
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
                                              zlib=True, complevel=2)
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


def test_production_publish_resume_and_manifest_recovery(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "bin"))
    monkeypatch.setitem(sys.modules, "test_static_forcing_mask", pilot)
    import apply_static_forcing_mask as production

    original, mask = fixture_files(tmp_path)
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
    assert production.publish(*args)["status"] == "published"
    assert production.publish(*args)["status"] == "already_verified"
    assert json.loads(manifest.read_text())["source_provenance"] == "preserve-me"
    journal = state / "19790515.json"
    report = json.loads(journal.read_text())
    report["status"] = "ready_to_publish"
    journal.write_text(json.dumps(report))
    manifest.unlink()
    assert production.publish(*args)["status"] == "published"
    assert json.loads(manifest.read_text())["source_provenance"] == "preserve-me"
    with pytest.raises(ValueError):
        production.publish(source, mask, tmp_path / "different-root", state, tmp_path / "work")
