import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "resolution_plan", Path(__file__).resolve().parents[1] / "bin/plan_forcing_resolution_layout.py"
)
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def test_only_year_directories_move(tmp_path):
    stream = tmp_path / "forcing/outputs/cnrfc/retro"
    for name in ("1981", "daily", "monthly", "experimental_test"):
        (stream / name).mkdir(parents=True)
    report = planner.inventory(tmp_path)
    assert len(report["proposed_moves"]) == 1
    assert report["proposed_moves"][0]["destination"].endswith("retro/hourly/1981")
    assert (stream / "1981").is_dir()  # Planning is read-only.
    assert not (stream / "hourly").exists()


def test_conflicts_and_migrated_layout(tmp_path):
    stream = tmp_path / "forcing/outputs/conus/baseline"
    (stream / "1981").mkdir(parents=True)
    (stream / "hourly/1981").mkdir(parents=True)
    assert planner.inventory(tmp_path)["conflicts"]
    (stream / "1981").rmdir()
    assert planner.inventory(tmp_path)["proposed_moves"] == []
    assert planner.inventory(tmp_path)["conflicts"] == []


def test_symlink_rejected(tmp_path):
    stream = tmp_path / "forcing/outputs/conus/nrt"
    stream.mkdir(parents=True)
    (stream / "hourly").symlink_to(tmp_path, target_is_directory=True)
    assert planner.inventory(tmp_path)["conflicts"]
