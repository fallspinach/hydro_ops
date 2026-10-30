import importlib
from pathlib import Path

import pandas as pd
import pytest


def test_region_selection(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    builder = importlib.import_module('build_ucrb_domain')
    frame = pd.DataFrame({'HUC2': ['14', '18'], 'Name': ['Upper Colorado Region', 'California Region']})
    assert builder.select_region(frame)['HUC2'].tolist() == ['14']
    with pytest.raises(ValueError, match='exactly one'):
        builder.select_region(frame.iloc[1:])
    with pytest.raises(ValueError, match='exactly one'):
        builder.select_region(pd.concat([frame, frame]))
    frame.loc[0, 'Name'] = 'Lower Colorado Region'
    with pytest.raises(ValueError, match='name mismatch'):
        builder.select_region(frame)
