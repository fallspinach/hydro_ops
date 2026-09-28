import importlib.util
import json
from pathlib import Path

import pytest

from hydro_ops.wrf_hydro import production


def helper():
    path = Path(__file__).resolve().parents[1] / "bin/submit_conus_retro_simulation.py"
    spec = importlib.util.spec_from_file_location("conus_submission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_predecessor(tmp_path, monkeypatch):
    module = helper()
    root = tmp_path / "nwm/runs/conus/retro/recovery"
    root.mkdir(parents=True)
    restart_root = tmp_path / "nwm/restarts/conus/retro/recovery/production/1980/01"
    paths = [str(restart_root / name) for name in
             ("RESTART.1980010100_DOMAIN1", "HYDRO_RST.1980-01-01_00:00_DOMAIN1")]
    marker = root / "1979-passed.json"
    marker.write_text(json.dumps({"passed": True, "end": "1980-01-01 00:00:00",
                                 "restarts": paths}))
    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **k: "123|COMPLETED|0:0\n")
    checked = []
    monkeypatch.setattr(production, "restart_check", lambda p, d: checked.append((p, d)))
    module.verify_completed_predecessor(tmp_path, "recovery", 1979, "123")
    assert [str(p) for p in checked[0][0]] == paths
    marker.write_text(json.dumps({"passed": False}))
    with pytest.raises(RuntimeError, match="acceptance"):
        module.verify_completed_predecessor(tmp_path, "recovery", 1979, "123")


@pytest.mark.parametrize("record", ["", "123|FAILED|1:0\n", "456|COMPLETED|0:0\n"])
def test_failed_or_unknown_predecessor(tmp_path, monkeypatch, record):
    module = helper()
    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **k: record)
    with pytest.raises(RuntimeError, match="accounting"):
        module.verify_completed_predecessor(tmp_path, "recovery", 1979, "123")
