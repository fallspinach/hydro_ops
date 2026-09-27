import importlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def test_unique_baseline_dependencies(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('benchmark_nrt_revision_pool')
    days = [date(2026, 9, d) for d in (18, 19, 20)]
    required = tool.dependency_days(days, dict.fromkeys(days, True))
    assert required == [date(2026, 9, d) for d in range(17, 22)]
    assert len(required) == len(set(required))
    assert tool.dependency_days(days, dict.fromkeys(days, False)) == days


@pytest.mark.parametrize('mutate', [False, True])
def test_prepared_baselines_are_not_rebuilt(tmp_path, monkeypatch, mutate):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('benchmark_nrt_revision_pool')
    calls = []
    day = date(2026, 9, 18)

    class FakeEngine:
        def __init__(self, root, work, as_of, *, baseline_root, output_root):
            self.baseline = baseline_root

        def prism_paths(self, day):
            return [tmp_path / 'missing_prism.nc']

        def baseline_day(self, day):
            calls.append(day)
            path = tool.cycle.day_path(self.baseline, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('baseline')
            return path, {'input_fingerprint': 'same'}

        def produce_day(self, day):
            if mutate:
                tool.cycle.day_path(self.baseline, day).write_text('changed baseline')
            return {'day': str(day), 'prism_constrained': False}

    monkeypatch.setattr(tool.cycle, 'RecentNrt', FakeEngine)
    args = (tmp_path, tmp_path / 'campaign', tmp_path / 'scratch', [day], datetime.now(UTC), 1)
    if mutate:
        with pytest.raises(ValueError, match='Baseline changed'):
            tool.run_arm(*args)
    else:
        report = tool.run_arm(*args)
        assert report['status'] == 'passed'
    assert calls == [day]
