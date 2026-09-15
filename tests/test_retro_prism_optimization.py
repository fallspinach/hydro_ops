import importlib.util
from pathlib import Path

import pytest
from netCDF4 import Dataset

spec = importlib.util.spec_from_file_location(
    "retro_prism_benchmark", Path(__file__).resolve().parents[1] / "bin/benchmark_retro_prism_optimization.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_chunks_enabled_only_for_optimized_calendar(monkeypatch):
    monkeypatch.setenv("HYDRO_OPS_ARCHIVE_CHUNKS", "1")
    for script in module.STAGES:
        assert module.child_environment("reference", script)["HYDRO_OPS_ARCHIVE_CHUNKS"] == "0"
        expected = "1" if script == "materialize_calendar_forcing.py" else "0"
        assert module.child_environment("optimized", script)["HYDRO_OPS_ARCHIVE_CHUNKS"] == expected


def test_window_comparison_changes_only_window_writer():
    for script in module.STAGES:
        reference = module.child_environment('reference', script, True)['HYDRO_OPS_ARCHIVE_CHUNKS']
        optimized = module.child_environment('optimized', script, True)['HYDRO_OPS_ARCHIVE_CHUNKS']
        if script == 'produce_prism_constrained_daily.py':
            assert (reference, optimized) == ('0', '1')
        else:
            assert reference == optimized
    assert module.child_environment('reference', 'materialize_calendar_forcing.py', True)['HYDRO_OPS_ARCHIVE_CHUNKS'] == '1'


def fixture(path):
    with Dataset(path, "w") as data:
        data.createDimension("time", 2)
        data.createDimension("x", 2)
        for attr in ("archive_granularity", "prism_reconciliation_accepted", "prism_precipitation_revisions",
                     "forcing_domain_policy", "forcing_domain_content_audit", "forcing_static_mask_sha256"):
            data.setncattr(attr, "test")
        data.createVariable("T2D", "f4", ("time", "x"), fill_value=-9999)[:] = [[1, -9999], [3, 4]]
        data["T2D"].units = "K"
        data.createVariable("precip_timing_source_id", "u1", ("time", "x"))[:] = [[0, 0], [5, 6]]


def test_comparison_detects_auxiliary_and_physical_corruption(tmp_path):
    left, right = tmp_path / "a.nc", tmp_path / "b.nc"
    fixture(left)
    fixture(right)
    module.compare_files(left, right)
    for name in ("T2D", "precip_timing_source_id"):
        fixture(right)
        with Dataset(right, "r+") as data:
            data[name][1, 1] = 8
        with pytest.raises(ValueError, match="Values differ"):
            module.compare_files(left, right)


def test_comparison_detects_policy_and_variable_metadata(tmp_path):
    left, right = tmp_path / "a.nc", tmp_path / "b.nc"
    fixture(left)
    fixture(right)
    with Dataset(right, "r+") as data:
        data.forcing_domain_policy = "different"
    with pytest.raises(ValueError, match="Policy differs"):
        module.compare_files(left, right)
    fixture(right)
    with Dataset(right, "r+") as data:
        data["T2D"].units = "C"
    with pytest.raises(AssertionError):
        module.compare_files(left, right)
