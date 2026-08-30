from datetime import date
from pathlib import Path

from netCDF4 import Dataset


def _write_retro(path: Path, *, frequency: str, revision: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as data:
        data.createDimension("time", 24)
        data.setncattr("prism_constraint_frequency", frequency)
        data.setncattr("prism_reconciliation_accepted", "true")
        if revision:
            data.setncattr("prism_precipitation_revision", revision)


def test_daily_cleanup_requires_stable_revision(tmp_path: Path) -> None:
    import importlib.util

    script = Path(__file__).parents[1] / "bin/cleanup_stable_baseline.py"
    spec = importlib.util.spec_from_file_location("cleanup_stable_baseline", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = module.forcing_path(tmp_path, date(2025, 1, 1))
    _write_retro(path, frequency="daily", revision="provisional")
    assert not module.accepted_retro(path, frequency="daily")
    path.unlink()
    _write_retro(path, frequency="daily", revision="stable")
    assert module.accepted_retro(path, frequency="daily")


def test_monthly_cleanup_requires_complete_month_and_diagnostic(tmp_path: Path) -> None:
    import importlib.util

    script = Path(__file__).parents[1] / "bin/cleanup_stable_baseline.py"
    spec = importlib.util.spec_from_file_location("cleanup_stable_baseline_month", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for day in range(1, 29):
        _write_retro(
            module.forcing_path(tmp_path, date(1979, 2, day)), frequency="monthly"
        )
    diagnostic = tmp_path / "1979/197902.monthly_prism_diagnostics.nc"
    with Dataset(diagnostic, "w") as data:
        data.setncattr("precipitation_accepted", "true")
    assert module.accepted_month(tmp_path, 1979, 2)
    module.forcing_path(tmp_path, date(1979, 2, 28)).unlink()
    assert not module.accepted_month(tmp_path, 1979, 2)
