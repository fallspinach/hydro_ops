"""Source-aware NRT decisions without cluster or network access."""

import json
from types import SimpleNamespace

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
