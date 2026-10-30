# Model scheduling APIs use naive UTC datetimes.
# ruff: noqa: DTZ001
import importlib
import json
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture
def pilot(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    return importlib.import_module('run_cnrfc_hu18_pilot')


def test_bounded_selection_and_boundary(pilot):
    for name in ('19790101.LDASIN_DOMAIN1', '19810101.LDASIN_DOMAIN1',
                 '19801231.LDASIN_DOMAIN1.daily', '197901.LDASIN_DOMAIN1.monthly'):
        assert pilot.selected(Path(name))
    for name in ('19810102.LDASIN_DOMAIN1', '19810101.LDASIN_DOMAIN1.daily',
                 '198101.LDASIN_DOMAIN1.monthly', '197812.LDASIN_DOMAIN1.monthly'):
        assert not pilot.selected(Path(name))
    dates = list(pilot.required_hours())
    assert len(dates) == 731
    assert dates[0].name.startswith('19790102')
    assert dates[-1].name.startswith('19810101')


def test_parameter_pin(pilot, tmp_path):
    (tmp_path / 'parameters').mkdir()
    field = tmp_path / 'parameters/test.nc'
    field.write_bytes(b'known')
    (tmp_path / 'parameter_acceptance.json').write_text(json.dumps({
        'status': 'passed', 'files': {'test.nc': {'sha256': pilot.digest(field)}}}))
    pilot.check_parameters(tmp_path)
    field.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        pilot.check_parameters(tmp_path)


def test_continuation_bounds(pilot):
    start, stop = datetime(2026, 1, 1), datetime(2026, 3, 2)
    assert len(list(pilot.required_hours(start, stop))) == 61
    assert pilot.selected(Path('20260302.LDASIN_DOMAIN1'), start, stop)
    assert not pilot.selected(Path('20260303.LDASIN_DOMAIN1'), start, stop)
    assert not pilot.selected(Path('20260302.LDASIN_DOMAIN1.daily'), start, stop)
    assert pilot.selected(Path('202602.LDASIN_DOMAIN1.monthly'), start, stop)
    assert not pilot.selected(Path('202603.LDASIN_DOMAIN1.monthly'), start, stop)
    submit = importlib.import_module('submit_cnrfc_hu18_continuation')
    years = submit.schedule(datetime(1981, 1, 1), stop)
    assert len(years) == 46 and years[-1] == (2026, stop)


def test_candidate_isolation_and_gate(pilot, tmp_path):
    runner = importlib.import_module('run_cnrfc_production')
    outputs, restarts = runner.publication_roots(tmp_path, pilot.CAMPAIGN, domain=pilot.DOMAIN)
    assert outputs == tmp_path / 'nwm/outputs/cnrfc_hu18_v1/retro/hu18_pilot_v1'
    assert restarts == tmp_path / 'nwm/restarts/cnrfc_hu18_v1/retro/hu18_pilot_v1'
    with pytest.raises(ValueError, match='Unsupported'):
        runner.publication_roots(tmp_path, pilot.CAMPAIGN, 'nrt', pilot.DOMAIN)
    with pytest.raises(ValueError, match='warm-start gate'):
        runner.run(tmp_path, 1979, campaign=pilot.CAMPAIGN, domain=pilot.DOMAIN)
    gate = tmp_path / 'gate.json'
    gate.write_text(json.dumps({'status': 'passed', 'domain': 'cnrfc'}))
    with pytest.raises(ValueError, match='has not passed'):
        runner.run(tmp_path, 1979, campaign=pilot.CAMPAIGN, domain=pilot.DOMAIN, archive_gate=gate)
    with pytest.raises(ValueError, match='authorized year'):
        runner.run(tmp_path, 1981, campaign=pilot.CAMPAIGN, domain=pilot.DOMAIN, archive_gate=gate)
    assert not (tmp_path / 'nwm').exists()
