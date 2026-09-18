"""Retry selection and static-audit publication bookkeeping."""

import importlib.util
import json
from pathlib import Path

import pytest


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


@pytest.mark.parametrize("stream,enabled", [("retro", "1"), ("nrt", "0")])
def test_approved_writer_scope(tmp_path, stream, enabled):
    module = load("slurm/rebuild_post2020_forcing.py")
    receipt = tmp_path / "acceptance.json"
    receipt.write_text(json.dumps({"status": "passed", "days": list(range(7))}))
    task = {"stream": stream, "output_root": str(tmp_path / "forcing/outputs/conus" / stream),
            "writer_acceptance": str(receipt), "writer_all_optimizations": True,
            "writer_profile": "validated_chunks_v1" if stream == "retro" else "reference"}
    env = {"HYDRO_OPS_REBUILD_STATIC_ENVELOPE": "1", "HYDRO_OPS_RETRO_NEW_PRODUCTION": "1"}
    assert module.configure_writer(task, tmp_path, env)
    assert env["HYDRO_OPS_ARCHIVE_CHUNKS"] == enabled
    assert env["HYDRO_OPS_BENCH_FAST_MASK"] == enabled
    assert env["HYDRO_OPS_BENCH_MULTIDAY"] == "0"
    assert "HYDRO_OPS_RETRO_NEW_PRODUCTION" not in env
    task["output_root"] = str(tmp_path / "wrong")
    with pytest.raises(ValueError):
        module.configure_writer(task, tmp_path, env)
    receipt.write_text('{"status":"failed"}')
    with pytest.raises(ValueError):
        module.configure_writer(task, tmp_path, env)


def test_existing_worker_environment_unchanged():
    module = load("slurm/rebuild_post2020_forcing.py")
    env = {"HYDRO_OPS_ARCHIVE_CHUNKS": "0"}
    assert not module.configure_writer({}, Path("/unused"), env)
    assert env == {"HYDRO_OPS_ARCHIVE_CHUNKS": "0"}


def test_snapshot_expands_cancelled_array_tasks(monkeypatch):
    module = load("bin/retry_post2020_forcing_campaign.py")

    def output(command, **kwargs):
        assert "--array" in command
        return "123_5|CANCELLED by 10\n123_6|RUNNING\n" if command[0] == "sacct" else "123_6|RUNNING\n"

    monkeypatch.setattr(module.subprocess, "check_output", output)
    assert module.snapshot("123") == {5: "CANCELLED", 6: "RUNNING"}
