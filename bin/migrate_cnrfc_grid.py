"""Journaled expanded-CNRFC promotion and explicitly audited old-grid retirement."""
# Model calendars are naive UTC.
# ruff: noqa: DTZ001
import argparse
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import digest, discover, identity
from run_cnrfc_hu18_pilot import check_parameters

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json
from hydro_ops.wrf_hydro.production import restart_names

GRID = 'cnrfc_hu18_v1'
ROOTS = ('forcing/outputs', 'forcing/status', 'nwm/static/domains',
         'nwm/outputs', 'nwm/restarts', 'nwm/runs', 'nwm/status')
STATE = Path('nwm/status/cnrfc-domain-migration')
WORK = Path('nwm/work/cnrfc-domain-migration-v1')


def lidentity(path):
    item = path.lstat()
    return [item.st_ino, item.st_size, item.st_mtime_ns]


def rewrite(text, mapping):
    for old in sorted(mapping, key=len, reverse=True):
        text = text.replace(old, mapping[old])
    return text


def path_mapping():
    mapping = {prefix + '/' + GRID + '/': prefix + '/cnrfc/' for prefix in ROOTS}
    for kind in ('outputs', 'restarts'):
        for stream, campaign in (('retro', 'hu18_pilot_v1'), ('nrt', 'nrt_continuation_v1')):
            mapping[f'nwm/{kind}/{GRID}/{stream}/{campaign}'] = f'nwm/{kind}/cnrfc/{stream}'
    for suffix in ('nc', 'json'):
        mapping[f'nwm/static/domains/{GRID}/masks/cnrfc_hu18_masks.{suffix}'] = f'nwm/static/domains/cnrfc/masks/cnrfc_masks.{suffix}'
    mapping[f'nwm/static/domains/{GRID}/boundaries/cnrfc_hu18_boundaries.gpkg'] = 'nwm/static/domains/cnrfc/boundaries/cnrfc_boundaries.gpkg'
    mapping['nwm/static/domains/cnrfc/boundaries/official_cnrfc.gpkg'] = 'nwm/static/domains/cnrfc/boundaries/sources/official_cnrfc.gpkg'
    return mapping


def archive_audit(root, domain, stream, campaign, start, end, *, promoted=False):
    output = root / 'nwm/outputs' / domain / stream
    restarts = root / 'nwm/restarts' / domain / stream
    if not promoted:
        output /= campaign
        restarts /= campaign
    runs = root / 'nwm/runs' / domain / stream / campaign
    markers = sorted(runs.glob('*/accepted.json'))
    cursor, hours = start, 0
    for marker in markers:
        record = json.loads(marker.read_text())
        stop = datetime.fromisoformat(record['end'])
        if not record.get('passed') or record['start'] != str(cursor) or not cursor < stop <= end:
            raise ValueError(f'Acceptance gap/invalid segment: {marker}')
        days = (stop - cursor).days
        if (record.get('hourly_channel_records') != days * 24
                or record.get('daily_land_files') != days or record.get('daily_channel_files') != days):
            raise ValueError('Accepted segment output count mismatch')
        if any(not (restarts / stop.strftime('%Y/%m') / n).is_file() for n in restart_names(stop)):
            raise ValueError('Accepted endpoint restart pair missing')
        hours += record['hourly_channel_records']
        cursor = stop
    if cursor != end:
        raise ValueError('Model publication endpoint mismatch')
    days = {(start + timedelta(days=i)).strftime('%Y%m%d') for i in range((end-start).days)}
    for frequency, suffix in (('hourly', '.CHRTOUT_DOMAIN1'), ('daily', '.LDASOUT_DOMAIN1.daily'),
                              ('daily', '.CHRTOUT_DOMAIN1.daily')):
        paths = sorted((output / frequency).glob('*/*/*' + suffix))
        expected = days | {end.strftime('%Y%m%d')} if frequency == 'hourly' else days
        if len(paths) != len(expected) or {p.name[:8] for p in paths} != expected:
            raise ValueError(f'Published calendar coverage differs: {output} {suffix}')
        if frequency == 'hourly':
            record_count = 0
            for path in paths:
                receipt = json.loads(path.with_name(path.name + '.archive.json').read_text())
                if (receipt.get('status') != 'passed' or not receipt.get('all_variable_values_verified')
                        or receipt.get('published_identity') != identity(path)):
                    raise ValueError(f'Stale/unaccepted hourly archive: {path}')
                expected_records = (23 if path.name[:8] == start.strftime('%Y%m%d') else
                                    1 if path.name[:8] == end.strftime('%Y%m%d') else 24)
                if receipt.get('records') != expected_records:
                    raise ValueError('Hourly archive record count differs from interval-end boundaries')
                record_count += receipt['records']
            if record_count != hours:
                raise ValueError('Hourly archive record total differs from accepted model steps')
    return {'start': str(start), 'end': str(end), 'segments': len(markers),
            'days': len(days), 'hourly_records': hours}


def audit_expanded(root, *, promoted=False):
    domain = 'cnrfc' if promoted else GRID
    static = root / 'nwm/static/domains' / domain
    check_parameters(static)
    mask = static / 'masks' / ('cnrfc_masks.nc' if promoted else 'cnrfc_hu18_masks.nc')
    expected_hash = json.loads((static / 'approval.json').read_text())['mask_sha256']
    if digest(mask) != expected_hash:
        raise ValueError('Approved expanded mask changed')
    forcing = root / 'forcing/outputs' / domain
    paths = discover(forcing)
    if not paths:
        raise ValueError('Expanded forcing archive empty')
    for path in paths:
        receipt = json.loads(path.with_name(path.name + '.subset.json').read_text())
        if (receipt.get('status') != 'passed' or receipt.get('output_identity') != identity(path)
                or receipt.get('mask_sha256') != expected_hash
                or receipt.get('missing_active_values') != 0):
            raise ValueError(f'Stale/unaccepted expanded forcing: {path}')
    return {'forcing_files': len(paths), 'mask_sha256': expected_hash,
        'retro': archive_audit(root, domain, 'retro', 'hu18_pilot_v1',
            datetime(1979, 1, 2), datetime(2026, 4, 18), promoted=promoted),
        'nrt': archive_audit(root, domain, 'nrt', 'nrt_continuation_v1',
            datetime(2026, 4, 18), datetime(2026, 10, 29), promoted=promoted)}


def enumerate_files(base):
    """Never follow symlink directories into unrelated project data."""
    for directory, dirs, files in os.walk(base, followlinks=False):
        parent = Path(directory)
        for name in list(dirs):
            path = parent / name
            if path.is_symlink():
                dirs.remove(name)
                yield path
        for name in files:
            yield parent / name


def promote(root):
    state, work = root / STATE, root / WORK
    report = json.loads((state / 'state.json').read_text())
    if report.get('status') != 'preparing' or not report.get('blocked'):
        raise ValueError('Promotion must start from the blocked preparing state')
    queue = subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%j'], text=True)
    pipeline = state / 'pipeline-jobs.json'
    allowed = set(json.loads(pipeline.read_text()).values()) if pipeline.exists() else set()
    allowed.add(os.environ['SLURM_JOB_ID'])
    if any(('wrfh-cnrfc' in line or 'forcing-cnrfc' in line or 'subdomain-nrt' in line)
           and line.split('|', 1)[0] not in allowed for line in queue.splitlines()):
        raise RuntimeError('CNRFC jobs still queued/running; inspect before migration')
    with ExitStack() as stack:
        for domain in ('cnrfc', GRID):
            for relative in (f'forcing/outputs/{domain}/.subset-pilot.lock',
                             f'nwm/runs/{domain}/nrt/production.lock'):
                handle = stack.enter_context((root / relative).open('a'))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print('Auditing expanded publications and preserved restart pairs', flush=True)
        acceptance = audit_expanded(root)
        old_forcing = root / 'forcing/outputs/cnrfc'
        new_forcing = root / 'forcing/outputs' / GRID
        missing = {p.relative_to(old_forcing) for p in discover(old_forcing)} - {
            p.relative_to(new_forcing) for p in discover(new_forcing)}
        if missing:
            raise ValueError(f'Expanded forcing lacks old archive periods: {sorted(missing)[:5]}')
        old_latest = root / 'nwm/status/cnrfc/nrt-daily/latest.json'
        if old_latest.exists() and datetime.fromisoformat(json.loads(old_latest.read_text())['end']) > datetime(2026, 10, 29):
            raise ValueError('Original model is newer than the expanded replacement')
        retired = work / 'retired'
        work.mkdir(parents=True, exist_ok=False)
        roots = []
        for prefix in ROOTS:
            old, new = root / prefix / 'cnrfc', root / prefix / GRID
            if old.is_symlink() or new.is_symlink() or not old.is_dir() or not new.is_dir():
                raise ValueError(f'Missing/nonregular domain root: {prefix}')
            if old.stat().st_dev != new.stat().st_dev or old.stat().st_dev != work.stat().st_dev:
                raise ValueError('Same-filesystem renames are required')
            roots.append((old, new, retired / prefix / 'cnrfc'))
        plan_path = state / 'old-grid-deletion-plan.jsonl.gz'
        count = logical_bytes = 0
        with gzip.open(plan_path, 'wt') as manifest, tarfile.open(state / 'old-grid-provenance.tar.gz', 'w:gz') as archive:
            for old, _, saved in roots:
                for path in enumerate_files(old):
                    relative = path.relative_to(old)
                    item = {'path': str((saved / relative).relative_to(root)),
                            'old_path': str(path.relative_to(root)), 'identity': lidentity(path),
                            'symlink': path.is_symlink()}
                    if item['symlink']:
                        item['link_target'] = os.readlink(path)
                    manifest.write(json.dumps(item) + '\n')
                    count += 1
                    logical_bytes += item['identity'][1]
                    keep = (path.suffix in ('.json', '.jsonl') or path.name.startswith('namelist.')
                            or path.name in ('hydro.namelist', 'model.log')
                            or ('nwm/static/domains/cnrfc' in str(old)
                                and relative.parts[0] in ('boundaries', 'masks')))
                    if keep:
                        archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
        plan = {'status': 'audited', 'replacement_grid': GRID, 'acceptance': acceptance,
                'old_files': count, 'old_logical_bytes': logical_bytes,
                'deletion_manifest_sha256': digest(plan_path),
                'provenance_archive_sha256': digest(state / 'old-grid-provenance.tar.gz'),
                'moves': [{'old': str(a.relative_to(root)), 'expanded': str(b.relative_to(root)),
                           'retired': str(c.relative_to(root))} for a, b, c in roots],
                'deletion_policy': 'Explicit user-authorized retirement of superseded grid, not byte-equivalent baseline cleanup'}
        write_json(state / 'plan.json', plan)
        report.update(status='promoting', blocked=True, completed_moves=[])
        write_json(state / 'state.json', report)
        for old, new, saved in roots:
            saved.parent.mkdir(parents=True, exist_ok=True)
            old.rename(saved)
            new.rename(old)
            report['completed_moves'].append(str(old.relative_to(root)))
            write_json(state / 'state.json', report)
        # Promote publication directories, keeping campaign names only in runs/status.
        for kind in ('outputs', 'restarts'):
            for stream, campaign in (('retro', 'hu18_pilot_v1'), ('nrt', 'nrt_continuation_v1')):
                parent = root / 'nwm' / kind / 'cnrfc' / stream
                container = parent / campaign
                for path in list(container.iterdir()):
                    target = parent / path.name
                    if target.exists():
                        raise FileExistsError(target)
                    path.rename(target)
                container.rmdir()
        static = root / 'nwm/static/domains/cnrfc'
        for suffix in ('nc', 'json'):
            (static / f'masks/cnrfc_hu18_masks.{suffix}').rename(static / f'masks/cnrfc_masks.{suffix}')
        (static / 'boundaries/cnrfc_hu18_boundaries.gpkg').rename(static / 'boundaries/cnrfc_boundaries.gpkg')
        official = retired / 'nwm/static/domains/cnrfc/boundaries/official_cnrfc.gpkg'
        target = static / 'boundaries/sources/official_cnrfc.gpkg'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(official, target)
        if digest(official) != digest(target):
            raise ValueError('Official boundary preservation failed')
        mapping = path_mapping()
        updated = 0
        with gzip.open(state / 'metadata-before.jsonl.gz', 'wt') as backup:
            for old, _, _ in roots:
                for path in enumerate_files(old):
                    if path.is_symlink() or path.suffix not in ('.json', '.jsonl'):
                        continue
                    original = path.read_text()
                    translated = rewrite(original, mapping)
                    if translated == original:
                        continue
                    if path.suffix == '.json':
                        json.loads(translated)
                    else:
                        for line in translated.splitlines():
                            if line.strip():
                                json.loads(line)
                    backup.write(json.dumps({'path': str(path.relative_to(root)),
                        'sha256': hashlib.sha256(original.encode()).hexdigest(), 'content': original}) + '\n')
                    atomic_bytes(path, translated.encode())
                    updated += 1
        manifest = json.loads((static / 'manifest.json').read_text())
        manifest.update(domain='cnrfc', grid_version=GRID, parameters_extracted=True,
                        approved_for_production=True, canonical_promotion='promoted_pending_tests')
        write_json(static / 'manifest.json', manifest)
        parameters = json.loads((static / 'parameter_acceptance.json').read_text())
        for name, record in parameters['files'].items():
            if name.endswith('.json'):
                record.update(sha256=digest(static / 'parameters' / name), bytes=(static / 'parameters' / name).stat().st_size)
        parameters.update(domain='cnrfc', grid_version=GRID)
        write_json(static / 'parameter_acceptance.json', parameters)
        gate = json.loads((root / 'nwm/status/subdomain-nrt-catchup' / GRID / 'handoff-acceptance.json').read_text())
        gate.update(domain='cnrfc', grid_version=GRID,
            inherited_acceptance='nwm/status/subdomain-nrt-catchup/cnrfc_hu18_v1/handoff-acceptance.json')
        write_json(root / 'nwm/status/cnrfc/grid-promotion/handoff-acceptance.json', gate)
        post = audit_expanded(root, promoted=True)
        if post != acceptance:
            raise ValueError('Promotion changed publication acceptance/counts')
        report.update(status='promoted_pending_tests', blocked=True, metadata_rewritten=updated,
                      post_promotion_acceptance=post, old_files_quarantined=count,
                      old_logical_bytes=logical_bytes)
        write_json(state / 'state.json', report)
        print(json.dumps(report), flush=True)


def retirement_path(root, relative):
    path = root / relative
    retired = root / WORK / 'retired'
    if not path.is_relative_to(retired) or not path.parent.resolve().is_relative_to(retired.resolve()):
        raise ValueError('Deletion target escapes explicit retired-grid quarantine')
    return path


def retire(root, test_job, sync_job):
    state = root / STATE
    report = json.loads((state / 'state.json').read_text())
    if report.get('status') not in ('promoted_pending_tests', 'retiring') or not report.get('blocked'):
        raise ValueError('Old-grid retirement requires the blocked, promoted state')
    sync = json.loads((root / f'forcing/status/cnrfc/sync-{sync_job}/status.json').read_text())
    test_path = root / f'nwm/runs/cnrfc/nrt/cycles/job-{test_job}/cycle.json'
    test = json.loads(test_path.read_text())
    if (sync.get('status') != 'passed' or sync.get('failed') != 0
            or test.get('status') != 'complete' or not test.get('test') or test.get('rst_typ') != 1
            or test.get('daily_checkpoint_continuation') != 'passed'):
        raise ValueError('Promoted forcing synchronization/replay tests did not pass')
    # Validate new outputs/restarts before touching any quarantined old-grid bytes.
    acceptance = audit_expanded(root, promoted=True)
    plan = json.loads((state / 'plan.json').read_text())
    deletion = state / 'old-grid-deletion-plan.jsonl.gz'
    if (digest(deletion) != plan['deletion_manifest_sha256']
            or digest(state / 'old-grid-provenance.tar.gz') != plan['provenance_archive_sha256']):
        raise ValueError('Audited deletion plan or retained provenance changed')
    # Verify every exact target before beginning the irreversible unlink pass.
    with gzip.open(deletion, 'rt') as handle:
        for line in handle:
            entry = json.loads(line)
            path = retirement_path(root, entry['path'])
            if path.exists() or path.is_symlink():
                if lidentity(path) != entry['identity']:
                    raise ValueError(f'Quarantined file changed: {path}')
            elif report['status'] != 'retiring':
                raise FileNotFoundError(path)
    report.update(status='retiring', blocked=True, replay_test=str(test_path.relative_to(root)),
                  forcing_sync_job=sync_job, replacement_acceptance=acceptance)
    write_json(state / 'state.json', report)
    deleted = 0
    with gzip.open(deletion, 'rt') as handle, (state / 'deleted-files.jsonl').open('a', buffering=1) as journal:
        for line in handle:
            entry = json.loads(line)
            path = retirement_path(root, entry['path'])
            if path.exists() or path.is_symlink():
                if lidentity(path) != entry['identity']:
                    raise ValueError('Retired target changed during deletion')
                path.unlink()
                journal.write(json.dumps({'deleted': entry['path'], 'identity': entry['identity']}) + '\n')
                deleted += 1
    retired = root / WORK / 'retired'
    for directory, _, _ in os.walk(retired, topdown=False, followlinks=False):
        Path(directory).rmdir()
    static = root / 'nwm/static/domains/cnrfc'
    approved = json.loads((static / 'approval.json').read_text())
    approved.update(operational_cutover_approved=True, canonical_domain='cnrfc', grid_version=GRID)
    write_json(static / 'approval.json', approved)
    manifest = json.loads((static / 'manifest.json').read_text())
    manifest['canonical_promotion'] = 'complete'
    write_json(static / 'manifest.json', manifest)
    report.update(status='complete', blocked=False, old_files_deleted_this_pass=deleted,
                  old_grid_provenance=str((state / 'old-grid-provenance.tar.gz').relative_to(root)),
                  operational_submissions_restored=True)
    write_json(state / 'state.json', report)
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('promote', 'retire'), required=True)
    parser.add_argument('--test-job')
    parser.add_argument('--sync-job')
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Run archive-wide audit/migration on SLURM')
    root = Path(__file__).resolve().parents[1]
    with (root / STATE / 'migration.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.stage == 'promote':
            promote(root)
        else:
            if not args.test_job or not args.sync_job or not args.test_job.isdecimal() or not args.sync_job.isdecimal():
                parser.error('Retirement requires exact numeric test and sync job IDs')
            retire(root, args.test_job, args.sync_job)


if __name__ == '__main__':
    main()
