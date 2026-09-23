#!/usr/bin/env python3
"""Read-only replacement audit and explicit baseline deletion manifest; never deletes."""
import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.retro_publication import AUDIT, POLICY


def identity(path):
    stat = path.stat()
    return {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def evidence(path):
    before = identity(path)
    raw = path.read_bytes()
    if identity(path) != before:
        raise ValueError(f"Evidence changed: {path}")
    return json.loads(raw), {"path": str(path), "identity": before,
                                "sha256": hashlib.sha256(raw).hexdigest()}


def check(task):
    root, day, mask_hash = task
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    baseline = root/'forcing/outputs/conus/baseline'/relative
    retro = root/'forcing/outputs/conus/retro'/relative
    try:
        paths = [baseline, baseline.with_name(baseline.name+'.manifest.json')]
        for path in [*paths, retro]:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Missing/nonregular/symlink target: {path}")
        deletion = [{"path": str(p), "identity": identity(p)} for p in paths]
        original = identity(retro)
        manifest, manifest_evidence = evidence(retro.with_name(retro.name+'.manifest.json'))
        envelope = manifest['static_envelope']
        audit, audit_evidence = evidence(Path(envelope['audit']))
        with Dataset(retro) as ds:
            times = num2date(ds['time'][:], ds['time'].units,
                             calendar=getattr(ds['time'], 'calendar', 'standard'))
            checks = {
                'calendar_hours': [t.strftime('%Y%m%d%H%M%S') for t in times]
                == [day.strftime('%Y%m%d')+f'{h:02d}0000' for h in range(24)],
                'calendar_archive': getattr(ds, 'archive_granularity', '') == 'utc_calendar_day',
                'prism_accepted': getattr(ds, 'prism_reconciliation_accepted', '') == 'true',
                'domain_policy': getattr(ds, 'forcing_domain_policy', '') == POLICY,
                'content_audit': getattr(ds, 'forcing_domain_content_audit', '') == AUDIT,
                'mask': getattr(ds, 'forcing_static_mask_sha256', '') == mask_hash,
                'cnrfc': day < date(2020, 7, 1) or bool(getattr(ds, 'cnrfc_stage4_policy', '')),
            }
            revision = getattr(ds, 'prism_precipitation_revision', None)
            revisions = json.loads(getattr(ds, 'prism_precipitation_revisions', '{}'))
            checks['stable'] = revision == 'stable' if revision is not None else (
                bool(revisions) and set(revisions.values()) == {'stable'})
        checks.update(
            manifest_identity=envelope['published_identity'] == original,
            audit_identity=audit['published_identity'] == original,
            audit_published=audit['status'] == 'published',
            manifest_mask=envelope['mask_sha256'] == mask_hash,
            audit_mask=audit['mask_sha256'] == mask_hash,
            checksum_evidence=bool(envelope['file_sha256'])
            and audit['candidate_sha256'] == envelope['file_sha256'],
        )
        if not all(checks.values()):
            raise ValueError(f"Failed checks: {[k for k, v in checks.items() if not v]}")
        if identity(retro) != original or any(identity(Path(p['path'])) != p['identity'] for p in deletion):
            raise ValueError('File changed during audit')
        return {"day": str(day), "status": 'eligible', "delete": deletion,
                    "replacement": {"path": str(retro), "identity": original,
                                     "audited_sha256": envelope['file_sha256']},
                    "evidence": [manifest_evidence, audit_evidence], "checks": checks}
    except (OSError, ValueError, RuntimeError, KeyError, AttributeError, TypeError) as error:
        return {"day": str(day), "status": 'blocked', "error": str(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path('.'))
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.end < args.start or not 1 <= args.workers <= 8:
        parser.error('Invalid range or workers (1–8)')
    if args.output.exists():
        raise FileExistsError(args.output)
    root = args.project_root.resolve()
    with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
        mask_hash = str(ds.keep_sha256)
    days = [args.start+timedelta(days=i) for i in range((args.end-args.start).days+1)]
    entries = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for item in pool.map(check, [(root, d, mask_hash) for d in days], chunksize=8):
            entries.append(item)
            if len(entries) % 250 == 0:
                print(json.dumps({"checked": len(entries), "total": len(days)}), flush=True)
    eligible = [e for e in entries if e['status'] == 'eligible']
    report = {"created": datetime.now(UTC).isoformat(), "mode": 'plan_only_no_deletion',
                  "status": 'passed' if len(eligible) == len(entries) else 'blocked',
                  "start": str(args.start), "end": str(args.end), "mask_sha256": mask_hash,
                  "eligible_days": len(eligible), "blocked_days": len(entries)-len(eligible),
                  "bytes_planned": sum(p['identity']['bytes'] for e in eligible for p in e['delete']),
                  "note": 'Headers and identity-matched existing audits checked; data hashes not recomputed. '
                       'Recheck identities and active consumers before any deletion.',
                  "retained_boundary_days": [str(args.start-timedelta(days=1)),
                                             str(args.end+timedelta(days=1))], "entries": entries}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(args.output.name+'.part')
    partial.write_text(json.dumps(report, indent=2)+'\n')
    partial.replace(args.output)
    print(json.dumps({k: v for k, v in report.items() if k != 'entries'}), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
