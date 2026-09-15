"""Retry selection and static-audit publication bookkeeping."""

import importlib.util
import json
from pathlib import Path


def load(relative):
    spec = importlib.util.spec_from_file_location("worker", Path(__file__).parents[1] / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retry_does_not_select_running_or_completed_batches():
    module = load("bin/retry_post2020_forcing_campaign.py")
    assert module.selected_indices({0: "COMPLETED", 1: "RUNNING", 2: "FAILED",
                                    3: "PENDING", 4: "COMPLETING"}) == [2, 3]


def test_permanent_manifest_uses_destination_identity(tmp_path):
    module = load("slurm/rebuild_post2020_forcing.py")
    source, destination = tmp_path / "candidate.nc", tmp_path / "published.nc"
    source.write_bytes(b"checked candidate")
    destination.write_bytes(source.read_bytes())
    staged = {"inode": source.stat().st_ino, "bytes": source.stat().st_size,
              "mtime_ns": source.stat().st_mtime_ns}
    record = {"daily_file": str(source), "prism_windows": "preserve",
              "static_envelope": {"published_identity": staged, "file_sha256": "verified-before-rename"}}
    source.with_name(source.name + ".manifest.json").write_text(json.dumps(record))
    module.finalize_static_manifest(source, destination)
    final = json.loads(destination.with_name(destination.name + ".manifest.json").read_text())
    assert final["daily_file"] == str(destination)
    assert final["prism_windows"] == "preserve"
    assert final["static_envelope"]["staged_identity"] == staged
    assert final["static_envelope"]["published_identity"]["inode"] == destination.stat().st_ino
