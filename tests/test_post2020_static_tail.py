import importlib.util
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def test_inspection_requires_corrections_and_complete_hours(tmp_path, monkeypatch):
    bin_path = Path(__file__).resolve().parents[1] / "bin"
    monkeypatch.syspath_prepend(str(bin_path))
    spec = importlib.util.spec_from_file_location(
        "static_tail", bin_path / "repair_post2020_static_tail.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "20201014.LDASIN_DOMAIN1"
    with Dataset(path, "w") as d:
        d.createDimension("time", 24)
        t = d.createVariable("time", "f8", ("time",))
        t.units = "hours since 2020-10-14 00:00:00"
        t[:] = np.arange(24)
        d.forcing_domain_policy = "nldas2_rectangle_nearest_valid_v1"
        d.forcing_recovery_policy = "cnrfc_prism_domain_rebuild_v1"
        d.archive_granularity = "utc_calendar_day"
        d.prism_reconciliation_accepted = "true"
        d.cnrfc_stage4_policy = "six-hour corrected"
    assert module.inspect(path)["cnrfc_stage4_policy"] == "six-hour corrected"
    with Dataset(path, "r+") as d:
        d.forcing_domain_policy = "nldas2_active_gaps_preserve_inactive_v3"
    assert module.inspect(path)["forcing_domain_policy"].endswith("v3")
    with Dataset(path, "r+") as d:
        d.cnrfc_stage4_policy = ""
    with pytest.raises(ValueError, match="provenance"):
        module.inspect(path)
    with Dataset(path, "r+") as d:
        d.cnrfc_stage4_policy = "six-hour corrected"
        d["time"][23] = 24
    with pytest.raises(ValueError, match="complete UTC"):
        module.inspect(path)
