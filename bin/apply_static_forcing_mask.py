"""Validated, resumable publication of the historical NLDAS-2 static envelope."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import test_static_forcing_mask as clip
from netCDF4 import Dataset

POLICY = "nldas2_seven_met_static_envelope_v4"
AUDIT = "all_records_active_retained_unchanged_outside_envelope_missing_v4"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with part.open("w") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


def identity(path):
    stat = path.stat()
    return {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def publish(path, mask, root, state, work):
    """Only publish within the authorized retro date range; never fill missing data."""
    path, root = path.resolve(), root.resolve()
    relative = path.relative_to(root)
    day = path.name.split(".")[0]
    if (len(relative.parts) != 3 or not day.isdigit() or len(day) != 8
            or not "19790101" <= day <= "20021231"
            or relative.parts[:2] != (day[:4], day[4:6])
            or path.name != f"{day}.LDASIN_DOMAIN1"):
        raise ValueError(f"Target outside authorized historical retro archive: {path}")
    state.mkdir(parents=True, exist_ok=True)
    with (state / f"{day}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _locked_publish(path, mask, state, work, day)


def _locked_publish(path, mask, state, work, day):
    report_path = state / f"{day}.json"
    manifest_path = path.with_name(path.name + ".manifest.json")
    with Dataset(mask) as grid:
        if grid.policy != POLICY or grid.status != "operational_historical_archive":
            raise ValueError("Mask is not the approved historical static asset")
        mask_hash = grid.keep_sha256
    with Dataset(path) as source:
        current_policy = str(getattr(source, "forcing_domain_policy", ""))
        current_hash = str(getattr(source, "forcing_static_mask_sha256", ""))
        if current_policy == "nldas2_rectangle_nwm_active_land_v2":
            raise ValueError("Retired v2 file needs source restoration before static clipping")
    old_report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if current_policy == POLICY and current_hash == mask_hash:
        if old_report.get("status") == "published" and old_report.get("published_identity") == identity(path):
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            if manifest.get("static_envelope", {}).get("published_identity") == identity(path):
                return {"path": str(path), "status": "already_verified"}
        # Recover the small manifest transaction if interrupted after NetCDF rename.
        if old_report.get("status") == "ready_to_publish" and file_hash(path) == old_report.get("candidate_sha256"):
            return finalize(path, manifest_path, report_path, old_report)
        raise ValueError("Current-policy file lacks matching durable publication audit; investigate before rewriting")
    before = identity(path)
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    candidate = path.with_name(f".{path.name}.static-envelope-v4.candidate")
    candidate_audit = candidate.with_name(candidate.name + ".pilot-audit.json")
    # These are exclusively owned temporary outputs; source remains untouched.
    for temporary in (candidate, candidate_audit, candidate.with_name(candidate.name + ".part")):
        temporary.unlink(missing_ok=True)
    report = clip.run(path, mask, candidate, work)
    with Dataset(candidate, "r+") as data:
        data.forcing_domain_policy = POLICY
        data.forcing_domain_content_audit = AUDIT
        data.forcing_static_mask_status = "operational_historical_archive"
        data.forcing_active_mask_rule = "preserve all existing values inside static envelope; require complete active cells; mask all eight fields outside"
        data.forcing_static_mask_applied_utc = datetime.now(UTC).isoformat()
    if identity(path) != before:
        raise ValueError("Source changed before publication")
    report.update(status="ready_to_publish", output=str(path), previous_manifest=previous_manifest,
                  policy=POLICY, mask_sha256=mask_hash, original_identity=before,
                  candidate_sha256=file_hash(candidate), output_bytes=candidate.stat().st_size,
                  note="Historical production clipping; all active and retained values verified unchanged")
    atomic_json(report_path, report)
    os.replace(candidate, path)
    result = finalize(path, manifest_path, report_path, report)
    candidate_audit.unlink(missing_ok=True)
    return result


def finalize(path, manifest_path, report_path, report):
    report.update(status="published", published_identity=identity(path), published_utc=datetime.now(UTC).isoformat())
    manifest = dict(report.get("previous_manifest") or {})
    manifest.setdefault("daily_file", str(path))
    manifest.setdefault("day", f"{path.name[:4]}-{path.name[4:6]}-{path.name[6:8]}")
    # Preserve existing PRISM/source provenance and verification; do not fabricate
    # a 24-source hourly manifest for monthly or partial-day products.
    manifest["static_envelope"] = {"policy": POLICY, "mask": report["mask"],
        "mask_sha256": report["mask_sha256"], "audit": str(report_path.resolve()),
        "published_identity": report["published_identity"], "published_utc": report["published_utc"],
        "active_values_unchanged": True, "retained_values_unchanged": True,
        "valid_outside_mask": 0, "file_sha256": report["candidate_sha256"]}
    atomic_json(manifest_path, manifest)
    atomic_json(report_path, report)
    return {"path": str(path), "status": "published", "audit": str(report_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("path", "mask", "root", "state", "work"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.path, args.mask, args.root, args.state, args.work)), flush=True)


if __name__ == "__main__":
    main()
