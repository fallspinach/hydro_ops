"""Final acceptance must identify its failed gate without weakening checks."""

import importlib.util
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def load_worker():
    spec = importlib.util.spec_from_file_location("calendar_batch", Path(__file__).parents[1] / "slurm/produce_prism_calendar_batch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_publication(path):
    with Dataset(path, "w") as data:
        data.createDimension("time", 24)
        time = data.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-12-13 00:00:00"
        time[:] = np.arange(24)
        data.archive_granularity = "utc_calendar_day"
        data.prism_reconciliation_accepted = "true"
        data.forcing_domain_policy = "nldas2_active_gaps_preserve_inactive_v3"
        data.forcing_domain_content_audit = "all_records_active_complete_outside_masked_inactive_preserved_v3"
        data.cnrfc_stage4_policy = "hourly Stage-IV excluded; retain alternate composite without six-hour constraint"


@pytest.mark.parametrize("attribute", ["archive_granularity", "prism_reconciliation_accepted", "forcing_domain_policy", "forcing_domain_content_audit", "cnrfc_stage4_policy"])
def test_named_metadata_failure(tmp_path, attribute, capsys):
    path = tmp_path / "day.nc"
    make_publication(path)
    worker = load_worker()
    worker.validate_publication(path, date(2020, 12, 13))
    with Dataset(path, "r+") as data:
        data.delncattr(attribute)
    with pytest.raises(ValueError, match=attribute):
        worker.validate_publication(path, date(2020, 12, 13))
    assert '"failed_checks": ["' + attribute + '"]' in capsys.readouterr().out


def test_duplicate_hour_rejected(tmp_path):
    path = tmp_path / "day.nc"
    make_publication(path)
    with Dataset(path, "r+") as data:
        data["time"][12] = 11
    with pytest.raises(ValueError, match="utc_hour_sequence"):
        load_worker().validate_publication(path, date(2020, 12, 13))
