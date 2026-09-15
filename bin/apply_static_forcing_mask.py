"""Validated, resumable publication of the historical NLDAS-2 static envelope."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
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


def publish(path, mask, root, state, work, *, staged_rebuild=False, staged_production=False, fast=False):
    """Clip historical archives or explicitly owned post-2020 rebuild staging."""
    path, root = path.resolve(), root.resolve()
    relative = path.relative_to(root)
    day = path.name.split(".")[0]
    allowed_dates = "20201014" <= day <= "20991231" if staged_rebuild else "19790101" <= day <= "20021231"
    if staged_production:
        if staged_rebuild:
            raise ValueError("Select only one staging scope")
        allowed_dates = "20030101" <= day <= "20201013"
    if (len(relative.parts) != 3 or not day.isdigit() or len(day) != 8
            or not allowed_dates
            or relative.parts[:2] != (day[:4], day[4:6])
            or path.name != f"{day}.LDASIN_DOMAIN1"):
        raise ValueError(f"Target outside authorized historical retro archive: {path}")
    if staged_rebuild or staged_production:
        # No expansion of the in-place historical archive sweep: this mode only
        # accepts privately owned candidates beneath the supplied job work root.
        root.relative_to(work.resolve())
        with Dataset(path) as data:
            if (getattr(data, "archive_granularity", "") != "utc_calendar_day"
                    or str(getattr(data, "prism_reconciliation_accepted", "")).lower() != "true"
                    or ((staged_rebuild or day >= "20200701")
                        and not getattr(data, "cnrfc_stage4_policy", ""))):
                raise ValueError("Staged rebuild lacks accepted calendar PRISM/CNRFC provenance")
    state.mkdir(parents=True, exist_ok=True)
    with (state / f"{day}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _locked_publish(path, mask, state, work, day, staged_rebuild=staged_rebuild,
                               staged_production=staged_production, fast=fast)


def fast_clip(path, mask, candidate, work, day):
    from benchmark_static_mask_chunks import chunk_mask, verify, verify_chunks

    # This audit is embedded in the actual input file, not a stale sidecar.
    # Unknown/unaudited inputs still receive the full independent audit.
    with Dataset(path) as source:
        prior_audit = str(getattr(source, 'forcing_domain_content_audit', ''))
        trusted = prior_audit in (
            'all_records_all_8_fields_nldas2_rectangle_and_model_land_v1',
            'all_records_active_complete_outside_masked_inactive_preserved_v3',
        )
    with tempfile.TemporaryDirectory(prefix='production-static-chunks-', dir=work) as temporary:
        staged, output = Path(temporary)/'source.nc', Path(temporary)/'masked.nc'
        shutil.copyfile(path, staged)
        report = chunk_mask(staged, mask, output)
        report['integrity'] = verify_chunks(staged, mask, output)
        full = not trusted or day.endswith('01')
        if full:
            report['full_verification_seconds'] = verify(staged, mask, output)
        report.update(mask=str(mask.resolve()), source=str(path),
                      prior_content_audit=prior_audit, full_readback=full,
                      validation_mode='full' if full else 'embedded_source_audit_plus_chunk_integrity')
        part = candidate.with_name(candidate.name+'.part')
        shutil.copyfile(output, part)
        with part.open('rb') as handle:
            os.fsync(handle.fileno())
        if file_hash(output) != file_hash(part):
            raise ValueError('Chunk-writer publication transfer checksum mismatch')
        os.replace(part, candidate)
    return report


def _locked_publish(path, mask, state, work, day, *, staged_rebuild=False, staged_production=False, fast=False):
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
    if fast:
        work.mkdir(parents=True, exist_ok=True)
        report = fast_clip(path, mask, candidate, work, day)
    else:
        report = clip.run(path, mask, candidate, work)
    with Dataset(candidate, "r+") as data:
        data.forcing_domain_policy = POLICY
        data.forcing_static_mask_sha256 = mask_hash
        data.forcing_static_mask = str(mask.resolve())
        data.forcing_domain_content_audit = AUDIT
        data.forcing_static_mask_status = "operational_post2020_rebuild" if staged_rebuild else "operational_historical_archive"
        if staged_production:
            data.forcing_static_mask_status = "operational_staged_retro_production"
        data.forcing_active_mask_rule = "preserve all existing values inside static envelope; require complete active cells; mask all eight fields outside"
        data.forcing_static_mask_applied_utc = datetime.now(UTC).isoformat()
    if identity(path) != before:
        raise ValueError("Source changed before publication")
    report.update(status="ready_to_publish", output=str(path), previous_manifest=previous_manifest,
                  policy=POLICY, mask_sha256=mask_hash, original_identity=before,
                  candidate_sha256=file_hash(candidate), output_bytes=candidate.stat().st_size,
                  note="Production clipping; all active and retained values verified unchanged",
                  scope="post2020_staged_rebuild" if staged_rebuild else "historical_archive")
    if staged_production:
        report["scope"] = "2003_20201013_staged_production"
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


def transfer_publication(path, destination, state):
    """Checksum-verified final copy; rebind the durable audit to permanent identity."""
    report_path = state / f"{path.name[:8]}.json"
    report = json.loads(report_path.read_text())
    if report["status"] != "published" or report["published_identity"] != identity(path):
        raise ValueError("Staged publication lacks matching completed audit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + f".{os.getpid()}.part")
    try:
        shutil.copyfile(path, partial)
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        if file_hash(partial) != report["candidate_sha256"]:
            raise ValueError("Final publication transfer checksum mismatch")
        manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text())
        manifest["daily_file"] = str(destination)
        report.update(status="ready_to_publish", output=str(destination),
                      staged_source=str(path), previous_manifest=manifest)
        atomic_json(report_path, report)
        os.replace(partial, destination)
        return finalize(destination, destination.with_name(destination.name + ".manifest.json"),
                        report_path, report)
    finally:
        partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("path", "mask", "root", "state", "work"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--staged-rebuild", action="store_true")
    parser.add_argument("--staged-production", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--publish-to", type=Path)
    args = parser.parse_args()
    if args.publish_to and (not args.staged_production or args.publish_to.name != args.path.name):
        parser.error("Final transfer requires staged production and an unchanged filename")
    print(json.dumps(publish(args.path, args.mask, args.root, args.state, args.work,
                             staged_rebuild=args.staged_rebuild,
                             staged_production=args.staged_production, fast=args.fast)), flush=True)
    if args.publish_to:
        print(json.dumps(transfer_publication(args.path, args.publish_to, args.state)), flush=True)


if __name__ == "__main__":
    main()
