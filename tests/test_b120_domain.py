import importlib
import json
from pathlib import Path

import pytest


def test_parameter_stage_requires_accepted_unchanged_masks(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('prepare_b120_domain')
    (tmp_path / 'manifest.json').write_text(json.dumps({'status': 'failed'}))
    with pytest.raises(ValueError, match='Masks did not pass'):
        tool.parameters(tmp_path, tmp_path)
    (tmp_path / 'masks').mkdir()
    mask = tmp_path / 'masks/b120_masks.nc'
    mask.write_bytes(b'original')
    checksum = tool.digest(mask)
    (tmp_path / 'manifest.json').write_text(json.dumps({'status': 'passed', 'mask_sha256': checksum}))
    mask.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Masks did not pass'):
        tool.parameters(tmp_path, tmp_path)
    assert not (tmp_path / 'parameters').exists()
