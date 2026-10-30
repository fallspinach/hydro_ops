# Model scheduling APIs use naive UTC.
# ruff: noqa: DTZ001
import importlib
from datetime import datetime
from pathlib import Path

import pytest


def test_ucrb_isolation_and_boundary(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    runner = importlib.import_module('run_cnrfc_production')
    outputs, restarts = runner.publication_roots(tmp_path, 'production_v1', domain='ucrb')
    assert outputs == tmp_path / 'nwm/outputs/ucrb/retro/production_v1'
    assert restarts == tmp_path / 'nwm/restarts/ucrb/retro/production_v1'
    with pytest.raises(ValueError, match='Unsupported'):
        runner.publication_roots(tmp_path, 'production_v1', 'nrt', 'ucrb')
    with pytest.raises(ValueError, match='warm-start gate'):
        runner.run(tmp_path, 1979, campaign='production_v1', domain='ucrb')
    submit = importlib.import_module('submit_ucrb_production')
    first, end = datetime(1980, 12, 31), datetime(1981, 1, 1)
    for day in (first, end):
        path = tmp_path / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        path.parent.mkdir(parents=True, exist_ok=True); path.touch()
        assert not submit.forcing_ready(tmp_path, first, end)
        path.with_name(path.name + '.subset.json').touch()
    assert submit.forcing_ready(tmp_path, first, end)
