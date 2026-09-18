import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from netCDF4 import Dataset


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "bin" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_refresh_allows_only_stage4_missing_archives(tmp_path, monkeypatch):
    module = load("update_forcing")
    monkeypatch.setattr(module, "load_settings", lambda: SimpleNamespace(work_root=tmp_path))
    monkeypatch.setattr(module, "forcing_coverage", lambda settings: [])
    monkeypatch.setattr(module, "format_coverage", lambda reports: "")
    monkeypatch.setattr(module, "active_jobs", lambda user: set())
    monkeypatch.setattr(module, "refresh_dates", lambda *args: None)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([]) == 0
    assert len(commands) == 5
    assert all(("--allow-missing" in command) == ("stage4" in command) for command in commands)


def test_comparison_rejects_changed_data(tmp_path):
    module = load("benchmark_nrt_reconciliation")
    a, b = tmp_path / "a.nc", tmp_path / "b.nc"
    for path in (a, b):
        with Dataset(path, "w") as data:
            data.createDimension("time", 2)
            data.createVariable("T2D", "f4", ("time",))[:] = [280, 281]
    module.compare(a, b)
    with Dataset(b, "r+") as data:
        data["T2D"][1] = 282
    with pytest.raises(ValueError, match="Field mismatch"):
        module.compare(a, b)


def test_seed_invalidates_private_receipt_only(tmp_path):
    module = load("benchmark_nrt_reconciliation")
    source = tmp_path / "source"
    source.mkdir()
    original = source / "2026/09/20260915.LDASIN_DOMAIN1"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"accepted original")
    receipt = original.with_name(original.name + ".nrt-receipt.json")
    receipt.write_text(json.dumps({"input_fingerprint": "accepted", "published_identity": {}}))
    module.seed(source, tmp_path / "nrt")
    private = tmp_path / "nrt/2026/09/20260915.LDASIN_DOMAIN1"
    assert private.read_bytes() == original.read_bytes()
    assert json.loads(receipt.read_text())["input_fingerprint"] == "accepted"
    assert json.loads(private.with_name(private.name + ".nrt-receipt.json").read_text())["input_fingerprint"] == "test-revision-invalidated"
