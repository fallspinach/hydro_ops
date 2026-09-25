import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "summary_backfill", Path(__file__).resolve().parents[1] / "bin/backfill_forcing_summaries.py"
)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def test_nrt_receipt_rejects_stale_identity(tmp_path):
    from hydro_ops.forcing.nrt_cycle import identity
    path = tmp_path / 'forcing.nc'
    path.write_bytes(b'fixture')
    receipt = path.with_name(path.name + '.nrt-receipt.json')
    receipt.write_text(json.dumps({'status': 'passed', 'sha256': 'test',
                                   'published_identity': identity(path)}))
    assert backfill.audited(path, 'nrt') == identity(path)
    path.write_bytes(b'changed content')
    with pytest.raises(ValueError, match='stale'):
        backfill.audited(path, 'nrt')


def test_parallel_year_locks_exclude_overlaps_and_legacy_controller(tmp_path):
    a, b = date(1980, 1, 1), date(1981, 1, 1)
    with backfill.publication_locks(tmp_path, a, a, True):
        with backfill.publication_locks(tmp_path, b, b, True):
            pass
        with pytest.raises(BlockingIOError), backfill.publication_locks(tmp_path, a, b, True):
            pass
        with pytest.raises(BlockingIOError), backfill.publication_locks(tmp_path, b, b):
            pass
    with (backfill.publication_locks(tmp_path, a, a), pytest.raises(BlockingIOError),
          backfill.publication_locks(tmp_path, b, b, True)):
        pass


def test_partial_1979_start_skips_january_monthly_only():
    start, end = date(1979, 1, 2), date(1979, 12, 31)
    with pytest.raises(ValueError):
        backfill.requested_months(start, end)
    months = backfill.requested_months(start, end, True)
    assert len(months) == 11
    assert months[0][0] == date(1979, 2, 1)
    assert len(list(backfill.periods(start, end, "daily"))) == 364


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
    # Accept only an explicit, identity/hash-linked verified scratch transfer.
    staged = {**identity, "inode": identity["inode"] + 1}
    original_audit = json.loads(audit.read_text())
    linked_audit = {**original_audit, "published_identity": staged, "mask_sha256": "mask"}
    manifest["static_envelope"].update(staged_identity=staged, mask_sha256="mask",
        audit_scope="staged-candidate validation recorded in mask audit; permanent transfer checksum verified")
    sidecar.write_text(json.dumps(manifest))
    audit.write_text(json.dumps(linked_audit))
    assert backfill.audited(source) == identity
    for change in [{"published_identity": {**staged, "inode": 0}},
                   {"candidate_sha256": "wrong"}, {"mask_sha256": "wrong"}]:
        audit.write_text(json.dumps({**linked_audit, **change}))
        with pytest.raises(ValueError, match="audit"):
            backfill.audited(source)
    audit.write_text(json.dumps(linked_audit))
    manifest["static_envelope"]["audit_scope"] = "unverified copy"
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
    audit.write_text(json.dumps(original_audit))
    del manifest["verified"]
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
    report = json.loads(audit.read_text())
    report["fields"] = {name: {
        "records": 24, "missing_active_cell_hours": 0,
        "active_unchanged": True, "retained_unchanged": True,
        "valid_outside_mask": 0, "active_sha256": "digest",
    } for name in ("LWDOWN", "PSFC", "Q2D", "RAINRATE", "SWDOWN", "T2D", "U2D", "V2D")}
    audit.write_text(json.dumps(report))
    assert backfill.audited(source) == identity
    report["fields"]["RAINRATE"]["missing_active_cell_hours"] = 1
    audit.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
    report["fields"]["RAINRATE"]["missing_active_cell_hours"] = 0
    audit.write_text(json.dumps(report))
    manifest["verified"] = False
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
    manifest["verified"] = True
    sidecar.write_text(json.dumps(manifest))
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="audit"):
        backfill.audited(source)
