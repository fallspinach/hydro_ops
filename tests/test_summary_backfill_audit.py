import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "summary_backfill", Path(__file__).resolve().parents[1] / "bin/backfill_forcing_summaries.py"
)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def test_audit_identity_and_acceptance_required(tmp_path):
    source = tmp_path / "source.nc"
    source.write_bytes(b"fixture")
    stat = source.stat()
    identity = {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "status": "published",
                "published_identity": identity,
                "candidate_sha256": "fixture_digest",
            }
        )
    )
    manifest = {
        "verified": True,
        "static_envelope": {
            "policy": "nldas2_seven_met_static_envelope_v4",
            "audit": str(audit),
            "published_identity": identity,
            "active_values_unchanged": True,
            "retained_values_unchanged": True,
            "valid_outside_mask": 0,
            "file_sha256": "fixture_digest",
        },
    }
    sidecar = source.with_name(source.name + ".manifest.json")
    sidecar.write_text(json.dumps(manifest))
    assert backfill.audited(source) == identity
    manifest["verified"] = False
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
    manifest["verified"] = True
    sidecar.write_text(json.dumps(manifest))
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
