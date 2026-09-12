from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def load_repair_module():
    path = Path(__file__).parents[1] / "bin/repair_nwm_forcing_domain.py"
    spec = importlib.util.spec_from_file_location("repair_nwm_forcing_domain", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_mask_recognizes_netcdf_fill_value(tmp_path: Path) -> None:
    module = load_repair_module()
    path = tmp_path / "forcing.nc"
    with Dataset(path, "w") as data:
        data.createDimension("y", 1)
        data.createDimension("x", 2)
        variable = data.createVariable("SWDOWN", "f4", ("y", "x"), fill_value=-9.99e8)
        variable[:] = [[100.0, -9.99e8]]
    with Dataset(path) as data:
        missing = module.missing_mask(data["SWDOWN"], data["SWDOWN"][:])
    assert missing.tolist() == [[False, True]]


def make_case(tmp_path, module):
    latitude = np.array([[30, 30, 30], [20, 30, 30]], dtype="f4")
    longitude = np.full((2, 3), -100, dtype="f4")
    land = np.array([[1, 1, 2], [2, 1, 2]], dtype="f4")
    model = tmp_path / "model.nc"
    source = tmp_path / "source.nc"
    for path in (model, source):
        with Dataset(path, "w") as data:
            data.createDimension("time", 2)
            data.createDimension("y", 2)
            data.createDimension("x", 3)
            if path == model:
                for name, values in (("XLAT", latitude), ("XLONG", longitude), ("XLAND", land)):
                    data.createVariable(name, "f4", ("time", "y", "x"))[:] = values
            else:
                time = data.createVariable("time", "f8", ("time",))
                time.units = "hours since 1979-01-01 00:00:00"
                time[:] = [0, 1]
                for name, values in (("lat", latitude), ("lon", longitude)):
                    data.createVariable(name, "f4", ("y", "x"))[:] = values
                for i, name in enumerate(module.VARIABLES):
                    data.createVariable(name, "f4", ("time", "y", "x"), fill_value=-9.99e8)[:] = (
                        np.arange(12, dtype="f4").reshape(2, 2, 3) + i * 100
                    )
                data.setncattr("forcing_domain_policy", module.POLICY)
    return source, model, land == 1


def test_filter_preserves_in_domain_water_and_land_and_masks_outside(tmp_path):
    module = load_repair_module()
    source, model, land = make_case(tmp_path, module)
    destination = tmp_path / "masked.nc"
    report = module.repair(source, destination, model_mask_path=model, mask_inactive=True)
    assert report["complete"]
    assert not any(report["missing_model_land"].values())
    assert not any(report["valid_outside_domain"].values())
    assert not any(report["missing_domain"].values())
    with Dataset(source) as old, Dataset(destination) as new:
        domain = module.geographic_domain_mask(old["lat"][:], old["lon"][:])
        for name in module.VARIABLES:
            np.testing.assert_array_equal(new[name][:].data[:, land], old[name][:].data[:, land])
            np.testing.assert_array_equal(new[name][:].data[:, domain], old[name][:].data[:, domain])
            assert np.ma.getmaskarray(new[name][:])[:, ~domain].all()
        assert new.forcing_domain_policy == module.ACTIVE_POLICY
    repeated = module.repair(destination, destination, model_mask_path=model, mask_inactive=True)
    assert repeated["status"] == "already_repaired_and_validated"


def test_inactive_filter_fills_missing_active_cell_but_never_outside_boundary(tmp_path):
    module = load_repair_module()
    source, model, _ = make_case(tmp_path, module)
    with Dataset(source, "r+") as data:
        for name in module.VARIABLES:
            data[name][0, 0, 0] = -9.99e8
            data[name][0, 0, 2] = -9.99e8  # Inactive in-domain gap stays missing.
    report = module.repair(source, source, model_mask_path=model, mask_inactive=True)
    assert report["complete"]
    assert all(value == 1 for value in report["counts"].values())
    with Dataset(source) as data:
        for name in module.VARIABLES:
            assert np.ma.is_masked(data[name][0, 0, 2])
            assert np.ma.is_masked(data[name][0, 1, 0])
            assert not np.ma.is_masked(data[name][0, 1, 2])


def test_inactive_filter_refuses_misaligned_model_grid(tmp_path):
    module = load_repair_module()
    source, model, _ = make_case(tmp_path, module)
    with Dataset(model, "r+") as data:
        data["XLAT"][0, 0, 0] += 0.001
    with pytest.raises(ValueError, match="coordinates differ"):
        module.repair(source, source, model_mask_path=model, mask_inactive=True)


def test_preserve_active_rejects_existing_land_gaps_without_modifying_file(tmp_path):
    module = load_repair_module()
    source, model, _ = make_case(tmp_path, module)
    with Dataset(source, "r+") as data:
        data["T2D"][0, 0, 0] = -9.99e8
    before = source.read_bytes()
    with pytest.raises(ValueError, match="requires complete active-land"):
        module.repair(source, source, model_mask_path=model,
                      mask_inactive=True, preserve_active=True)
    assert source.read_bytes() == before


def test_retired_mask_cannot_be_relabelled_as_preserving_water_values(tmp_path):
    module = load_repair_module()
    source, model, _ = make_case(tmp_path, module)
    with Dataset(source, "r+") as data:
        data.setncattr("forcing_domain_policy", "nldas2_rectangle_nwm_active_land_v2")
    with pytest.raises(ValueError, match="restore inactive values"):
        module.repair(source, source, model_mask_path=model)


def test_reconstruct_inactive_cells_preserves_original_active_values(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "bin"))
    import restore_inactive_forcing_values as restore

    module = load_repair_module()
    original, model, land = make_case(tmp_path, module)
    candidate = tmp_path / "reconstructed.nc"
    module.repair(original, candidate, model_mask_path=model)
    with Dataset(candidate, "r+") as data:
        data.prism_reconciliation_accepted = "true"
        # Reconstructed active values intentionally differ: they must NOT be used.
        values = data["T2D"][:].filled(data["T2D"]._FillValue)
        values[:, land] += 99
        data["T2D"][:] = values
    with Dataset(original, "r+") as data:
        data.forcing_domain_policy = "nldas2_rectangle_nwm_active_land_v2"
        for name in module.VARIABLES:
            values = data[name][:].data.copy()
            values[:, ~land] = data[name]._FillValue
            data[name][:] = values
    before = module.completeness(original, model)["active_sha256"]
    monkeypatch.setattr("sys.argv", ["restore", str(original), str(candidate),
                        "--work-directory", str(tmp_path / "work"),
                        "--backup-directory", str(tmp_path / "backup"),
                        "--model-mask", str(model)])
    assert restore.main() == 0
    assert module.completeness(original, model)["active_sha256"] == before
    with Dataset(original) as data, Dataset(candidate) as reference:
        for name in module.VARIABLES:
            assert data[name][0, 0, 2] == reference[name][0, 0, 2]
    assert (tmp_path / "backup" / original.name).is_file()
