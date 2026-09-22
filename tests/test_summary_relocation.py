from datetime import date, timedelta

import pytest
from test_forcing_temporal_summary import chunk, run

from hydro_ops.forcing.temporal_summary import input_path, migration_stable_identity, summary_path


def test_approved_hourly_move_preserves_freshness(tmp_path):
    root = tmp_path / "forcing/outputs/conus/retro"
    day = date(1981, 1, 1)
    chunk(root, day)
    chunk(root, day + timedelta(days=1))
    output = root / "daily/test.nc"
    run(root, output, day, day + timedelta(days=1))
    (root / "hourly").mkdir()
    (root / "1981").rename(root / "hourly/1981")
    assert (
        run(root / "hourly", output, day, day + timedelta(days=1), skip_existing=True)["status"]
        == "unchanged"
    )
    (root / "hourly/1981/01/19810101.LDASIN_DOMAIN1").touch()
    with pytest.raises(ValueError, match="Stale"):
        run(root / "hourly", output, day, day + timedelta(days=1), skip_existing=True)


def test_no_arbitrary_path_normalization():
    for p in [
        "/tmp/hourly/1981/01/file",
        "/forcing/outputs/conus/retro/daily/1981/01/file",
        "/forcing/outputs/conus/retro/hourly/not-a-year/file",
    ]:
        assert migration_stable_identity({"path": p, "bytes": 1})["path"] == p


def test_summary_names_and_transition(tmp_path):
    day = date(1981, 1, 1)
    new = summary_path(tmp_path, day)
    assert new.name == "19810101.LDASIN_DOMAIN1.daily"
    assert summary_path(tmp_path, day, monthly=True).name == "198101.LDASIN_DOMAIN1.monthly"
    new.parent.mkdir(parents=True)
    old = new.with_name("19810101.FORCING_DAILY.nc")
    old.touch()
    assert input_path(tmp_path, day, daily=True) == old
    original = migration_stable_identity({"path": str(old), "bytes": 0})
    old.rename(new)
    assert input_path(tmp_path, day, daily=True) == new
    assert migration_stable_identity({"path": str(new), "bytes": 0}) == original
    old.touch()
    with pytest.raises(ValueError, match="Duplicate"):
        input_path(tmp_path, day, daily=True)
