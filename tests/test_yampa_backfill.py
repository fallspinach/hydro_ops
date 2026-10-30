import importlib
from pathlib import Path

import pytest


def test_parent_acceptance(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    check = importlib.import_module('backfill_yampa_forcing').check_parent
    good = {'status': 'passed', 'domain': 'ucrb', 'failed': 0,
            'total': 10, 'published': 4, 'skipped': 6}
    check(good)
    for change in ({'status': 'running'}, {'domain': 'cnrfc'}, {'failed': 1}, {'skipped': 5}, {'total': 0}):
        with pytest.raises(ValueError, match='not completely accepted'):
            check(good | change)
