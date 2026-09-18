"""Source-aware NRT decisions without cluster or network access."""

import fcntl
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.nrt_cycle import activation, fingerprint, identity, source_runs, up_to_date


def test_mixed_hour_source_runs_keep_whole_day_order():
    selections = [SimpleNamespace(product=p) for p in ["nldas2"] * 12 + ["hrrr"] * 12]
    assert list(source_runs(selections)) == [(0, 11), (12, 23)]
    assert list(source_runs(selections[:12])) == [(0, 11)]


def test_nldas_arrival_and_prism_revision_invalidate_receipt(tmp_path):
    output = tmp_path / "daily"
    output.write_bytes(b"verified output")
    previous = {"primary": ["hrrr"] * 24, "prism": {"missing": True}}
    receipt = {"status": "passed", "input_fingerprint": fingerprint(previous),
               "published_identity": identity(output)}
    assert up_to_date(output, receipt, fingerprint(previous))
    assert not up_to_date(output, receipt, fingerprint({**previous, "primary": ["nldas2"] * 24}))
    assert not up_to_date(output, receipt, fingerprint({**previous, "prism": {"mtime_ns": 123}}))
    output.write_bytes(b"unrecorded change")
    assert not up_to_date(output, receipt, fingerprint(previous))


def test_delayed_cycle_is_revisited(tmp_path):
    output = tmp_path / "daily"
    output.touch()
    receipt = {"status": "passed", "input_fingerprint": "same", "published_identity": identity(output),
               "retry_preferred_gfs_cycle": True}
    assert not up_to_date(output, receipt, "same")


def test_scheduled_activation_requires_real_acceptance(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/nrt_gfs.toml").write_text('enabled = true\nactivation_receipt = "acceptance.json"\n')
    assert not activation(tmp_path)
    receipt = tmp_path / "acceptance.json"
    receipt.write_text(json.dumps({"status": "failed", "policy": "source_aware_recent_nrt_v1"}))
    assert not activation(tmp_path)
    receipt.write_text(json.dumps({"status": "passed", "policy": "source_aware_recent_nrt_v1"}))
    assert activation(tmp_path)


def test_isolated_cycle_repeat_and_lock(tmp_path, monkeypatch):
    published = set()

    class Engine:
        def __init__(self, *args, **kwargs):
            self.output, self.layout, self.config = tmp_path / "output", None, {}

        def produce_day(self, day):
            status = "unchanged" if day in published else "published"
            published.add(day)
            return {"day": str(day), "status": status, "gfs_hours": 24}

    monkeypatch.setattr(nrt_cycle, "RecentNrt", Engine)
    monkeypatch.setattr(nrt_cycle, "replacement_backlog", lambda *args: [])
    state = tmp_path / "test-status"
    day = date(2026, 9, 15)
    args = (tmp_path, tmp_path / "scratch", day, day, datetime.now(UTC))
    first = nrt_cycle.run_cycle(*args, state_root=state)
    repeat = nrt_cycle.run_cycle(*args, state_root=state)
    assert first["days"][0]["status"] == "published"
    assert repeat["days"][0]["status"] == "unchanged"
    assert not (tmp_path / "forcing/status/nrt-gfs").exists()
    before = (state / "latest.json").read_bytes()
    with (state / "cycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            nrt_cycle.run_cycle(*args, state_root=state)
    assert (state / "latest.json").read_bytes() == before


def test_cycle_reports_failure_and_preserves_existing_output(tmp_path, monkeypatch):
    output = tmp_path / "previous.nc"
    output.write_bytes(b"previous accepted data")

    class Engine:
        def __init__(self, *args, **kwargs):
            self.output, self.layout, self.config = tmp_path, None, {}

        def produce_day(self, day):
            raise RuntimeError("Missing source bundle")

    monkeypatch.setattr(nrt_cycle, "RecentNrt", Engine)
    monkeypatch.setattr(nrt_cycle, "replacement_backlog", lambda *args: [])
    day = date(2026, 9, 15)
    report = nrt_cycle.run_cycle(tmp_path, tmp_path / "scratch", day, day, datetime.now(UTC),
                                 state_root=tmp_path / "status")
    assert report["status"] == "failed"
    assert report["errors"][0]["error"] == "Missing source bundle"
    assert output.read_bytes() == b"previous accepted data"
