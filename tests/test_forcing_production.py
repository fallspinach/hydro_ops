from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.assemble import add_precipitation_to_ldasin, assemble_seven_field_hour
from hydro_ops.forcing.operations import OperationalLayout, discover_precipitation_candidates
from hydro_ops.forcing.prism_temperature import apply_daily_temperature_constraint
from hydro_ops.forcing.source_selection import select_hourly_source, source_path
from hydro_ops.forcing.weights import validate_weight_manifest


def test_source_path_and_structurally_valid_fallback(tmp_path: Path, monkeypatch) -> None:
    valid = datetime(2023, 7, 1, 6, tzinfo=UTC)
    nldas = tmp_path / "nldas"
    hrrr = tmp_path / "hrrr"
    hrrr_path = source_path("hrrr", hrrr, valid)
    hrrr_path.parent.mkdir(parents=True)
    hrrr_path.touch()
    monkeypatch.setattr(
        "hydro_ops.forcing.source_selection.inspect_forcing_file",
        lambda path, product: SimpleNamespace(
            valid=True, valid_time="2023-07-01T06:00:00", issues=()
        ),
    )
    selected = select_hourly_source(valid, nldas, hrrr)
    assert selected.product == "hrrr"
    assert selected.fallback_used
    assert "nldas2: missing" in selected.rejected[0]


def test_daily_prism_constraint_matches_extrema(tmp_path: Path) -> None:
    paths = []
    start = datetime(2023, 6, 30, 12, tzinfo=UTC)
    for hour in range(24):
        path = tmp_path / f"hour-{hour:02d}.nc"
        value = 285.0 + 5.0 * np.sin(2 * np.pi * hour / 24)
        xr.Dataset(
            {"T2D_PRELIM": (("time", "y", "x"), np.full((1, 2, 2), value))},
            attrs={"source_valid_time": (start + timedelta(hours=hour)).isoformat()},
        ).to_netcdf(path)
        paths.append(path)
    constraint = tmp_path / "constraint.nc"
    xr.Dataset(
        {
            "prism_tmin": (("y", "x"), np.full((2, 2), 275.0)),
            "prism_tmax": (("y", "x"), np.full((2, 2), 295.0)),
        }
    ).to_netcdf(constraint)
    output = tmp_path / "corrected.nc"
    apply_daily_temperature_constraint(paths[::-1], constraint, output)
    with xr.open_dataset(output) as data:
        np.testing.assert_allclose(data.T2D.min("time"), 275.0)
        np.testing.assert_allclose(data.T2D.max("time"), 295.0)
        assert np.all(data.temperature_range_scale == 2.0)


def _write_target(path: Path) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.createDimension("nv4", 4)
        for name in ("lat", "lon"):
            data.createVariable(name, "f8", ("y", "x"))[:] = 1
            data.createVariable(f"{name}_bnds", "f8", ("y", "x", "nv4"))[:] = 1
        data.createVariable("active_domain", "i1", ("y", "x"))[:] = 1


def _write_component(path: Path, names: tuple[str, ...], qc_name: str) -> None:
    with Dataset(path, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.setncatts(
            {
                "source_valid_time": "2023-07-01T06:00:00",
                "source_product": "nldas2",
            }
        )
        for name in names:
            data.createVariable(name, "f4", ("time", "y", "x"))[:] = 1
        data.createVariable(qc_name, "u2", ("time", "y", "x"))[:] = 0


def test_assemble_seven_field_hour(tmp_path: Path) -> None:
    target = tmp_path / "target.nc"
    thermo = tmp_path / "thermo.nc"
    radiation = tmp_path / "radiation.nc"
    output = tmp_path / "LDASIN.nc"
    _write_target(target)
    _write_component(
        thermo, ("T2D", "Q2D", "PSFC", "LWDOWN"), "thermodynamic_qc_flags"
    )
    _write_component(
        radiation, ("U2D", "V2D", "SWDOWN"), "radiation_wind_qc_flags"
    )
    assemble_seven_field_hour(thermo, radiation, target, output)
    with Dataset(output) as data:
        assert set(data.variables) >= {
            "T2D", "Q2D", "PSFC", "LWDOWN", "U2D", "V2D", "SWDOWN"
        }
        assert "RAINRATE" not in data.variables
        assert data["T2D"]._FillValue == np.float32(-9.99e8)
        assert data.getncattr("precipitation_status").startswith("not_present")

    precipitation = tmp_path / "precipitation.nc"
    with Dataset(precipitation, "w") as data:
        data.createDimension("time", 1)
        data.createDimension("y", 2)
        data.createDimension("x", 3)
        data.setncattr("valid_time", "2023-07-01T06:00:00")
        for name, dtype in (
            ("RAINRATE", "f4"),
            ("precip_source_id", "u1"),
            ("precip_confidence", "f4"),
            ("precip_qc_flags", "u2"),
        ):
            data.createVariable(name, dtype, ("time", "y", "x"))[:] = 1
    complete = tmp_path / "complete.nc"
    add_precipitation_to_ldasin(output, precipitation, complete)
    with Dataset(complete) as data:
        assert "RAINRATE" in data.variables
        assert data.getncattr("precipitation_status") == "present"


def test_operational_discovery_only_returns_present_candidates(tmp_path: Path) -> None:
    layout = OperationalLayout.project_defaults(tmp_path)
    valid = datetime(2026, 7, 24, 10, tzinfo=UTC)
    nldas = source_path("nldas2", layout.nldas2_root, valid)
    nldas.parent.mkdir(parents=True)
    nldas.touch()
    candidates, quality = discover_precipitation_candidates(valid, layout)
    assert candidates == {"nldas2": nldas}
    assert quality is None


def test_validate_weight_manifest_rejects_mismatch(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.nc"
    target = tmp_path / "target.nc"
    weights = tmp_path / "weights.nc"
    for path in (source, target, weights):
        path.touch()
    manifest = weights.with_suffix(".nc.manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "source_product": "nldas2",
                "source_grid_fingerprint": "wrong",
                "target_grid_fingerprint": "target",
                "method": "bilinear",
            }
        )
    )
    monkeypatch.setattr(
        "hydro_ops.forcing.weights.inspect_forcing_file",
        lambda path, product: SimpleNamespace(valid=True, issues=(), grid_fingerprint="source"),
    )
    monkeypatch.setattr("hydro_ops.forcing.weights.netcdf_grid_fingerprint", lambda *args: "target")
    with np.testing.assert_raises_regex(ValueError, "source_grid_fingerprint"):
        validate_weight_manifest(source, "nldas2", target, weights)
