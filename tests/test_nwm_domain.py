from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.nwm_domain import inspect_file, inventory


def test_missing_file_is_pending(tmp_path: Path) -> None:
    result = inspect_file(tmp_path / "RouteLink_CONUS.nc")
    assert result["status"] == "pending"


def test_route_link_schema(tmp_path: Path) -> None:
    path = tmp_path / "RouteLink_CONUS.nc"
    with Dataset(path, "w") as dataset:
        dataset.createDimension("feature_id", 2)
        for name in ("link", "from", "to", "Length", "So", "MusK", "MusX", "ascendingIndex"):
            dataset.createVariable(name, "f8", ("feature_id",))
    result = inspect_file(path)
    assert result["status"] == "compatible"
    assert result["dimensions"]["feature_id"] == 2


def test_inventory_reports_external_run_blockers(tmp_path: Path) -> None:
    report = inventory(tmp_path)
    assert report["summary"]["pending"] == 15
    assert any(
        "LAKEPARM_CONUS.nc" in item for item in report["run_blockers_outside_domain_inventory"]
    )
