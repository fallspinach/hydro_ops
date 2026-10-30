import importlib
from pathlib import Path

import pandas as pd
import pytest


def test_exact_four_regions(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    builder = importlib.import_module('build_yampa_domain')
    frame = pd.DataFrame({'HUC8': list(builder.REGIONS), 'Name': list(builder.REGIONS.values())})
    assert len(builder.select_regions(frame)) == 4
    with pytest.raises(ValueError, match='exactly one'):
        builder.select_regions(frame.iloc[:3])
    with pytest.raises(ValueError, match='exactly one'):
        builder.select_regions(pd.concat([frame, frame.iloc[:1]]))
    frame.loc[0, 'Name'] = 'Wrong watershed'
    with pytest.raises(ValueError, match='mismatch'):
        builder.select_regions(frame)
