import importlib
import json
from pathlib import Path

import pytest


def test_approved_candidate_is_hash_pinned(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    tool = importlib.import_module('prepare_cnrfc_hu18_test')
    domain = tmp_path/'nwm/static/domains'/tool.DOMAIN
    mask = domain/'masks/cnrfc_hu18_masks.nc'
    boundary = domain/'boundaries/cnrfc_hu18_boundaries.gpkg'
    for path in (mask,boundary):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'approved geometry')
    approval = {'status':'approved','mask_sha256':tool.digest(mask),'boundary_sha256':tool.digest(boundary)}
    (domain/'approval.json').write_text(json.dumps(approval))
    assert tool.approved(tmp_path) == domain
    mask.write_bytes(b'changed geometry')
    with pytest.raises(ValueError,match='changed approved'):
        tool.approved(tmp_path)


def test_model_pilot_has_explicit_gate_and_separate_domain(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    tool = importlib.import_module('test_cnrfc_hu18_model')
    assert tool.DOMAIN == 'cnrfc_hu18_v1'
    assert tool.CASES == [('retro','1979-01-02'),('nrt','2026-04-12')]
