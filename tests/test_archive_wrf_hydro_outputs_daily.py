from importlib.machinery import SourceFileLoader
from pathlib import Path

module = SourceFileLoader(
    "archive_wrf_hydro_outputs_daily",
    str(Path(__file__).parents[1] / "bin" / "archive_wrf_hydro_outputs_daily.py"),
).load_module()


def test_group_hourly_outputs_excludes_daily_and_restart_files(tmp_path: Path) -> None:
    for name in (
        "201108260000.LDASOUT_DOMAIN1",
        "201108260100.LDASOUT_DOMAIN1",
        "201108260000.CHRTOUT_DOMAIN1",
        "20110826.LDASOUT_DOMAIN1",
        "RESTART.2011082600_DOMAIN1",
    ):
        (tmp_path / name).touch()

    groups = module.group_hourly_outputs(tmp_path)

    assert sorted(groups) == [
        ("20110826", "CHRTOUT_DOMAIN1"),
        ("20110826", "LDASOUT_DOMAIN1"),
    ]
    assert [path.name for path in groups[("20110826", "LDASOUT_DOMAIN1")]] == [
        "201108260000.LDASOUT_DOMAIN1",
        "201108260100.LDASOUT_DOMAIN1",
    ]
