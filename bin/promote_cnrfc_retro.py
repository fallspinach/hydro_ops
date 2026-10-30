"""Audit and promote the completed CNRFC recovery campaign; never touch CONUS."""
import argparse
import fcntl
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.gfs_publication import _atomic_json

CAMPAIGN = 'recovery_record_stack_v1'


def identity(path):
    s = path.stat()
    return [s.st_ino, s.st_size, s.st_mtime_ns]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from hydro_ops.cnrfc_domain import require_legacy_grid
    require_legacy_grid(root)
    runs = root / 'nwm/runs/cnrfc/retro' / CAMPAIGN
    output = root / 'nwm/outputs/cnrfc/retro'
    restart = root / 'nwm/restarts/cnrfc/retro'
    state = root / 'nwm/status/cnrfc/retro-promotion'
    state.mkdir(parents=True, exist_ok=True)
    marker = state / 'promotion.json'
    if marker.exists():
        raise RuntimeError('Promotion already started; inspect its journal before resuming')
    with (runs / 'production.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        jobs = subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%j'], text=True)
        if any('wrfh-cnrfc' in line for line in jobs.splitlines()):
            raise RuntimeError('CNRFC model jobs are still queued/running')
        moves = [(output / CAMPAIGN / kind, output / kind) for kind in ('hourly', 'daily')]
        moves += [(p, restart / p.name) for p in sorted((restart / CAMPAIGN).iterdir())]
        if any(not p.name.isdigit() or len(p.name) != 4 for p, _ in moves[2:]):
            raise ValueError('Unexpected restart campaign contents')
        for source, target in moves:
            if source.is_symlink() or not source.is_dir() or target.exists() or target.is_symlink():
                raise ValueError(f'Conflict or unsafe directory: {source} -> {target}')
            if source.stat().st_dev != target.parent.stat().st_dev:
                raise ValueError('Promotion must be same-filesystem renames')
        first = datetime.fromisoformat('1979-01-02 00:00:00')
        end = datetime.fromisoformat('2026-03-02 00:00:00')
        cursor = first
        accepted = sorted(runs.glob('[0-9][0-9][0-9][0-9][0-9][0-9]/accepted.json'))
        for path in accepted:
            record = json.loads(path.read_text())
            stop = datetime.fromisoformat(record['end'])
            if not record.get('passed') or datetime.fromisoformat(record['start']) != cursor:
                raise ValueError(f'Acceptance gap/failure: {path}')
            days = (stop - cursor).days
            for key, expected in [('hourly_channel_records', days * 24),
                                  ('daily_land_files', days), ('daily_channel_files', days)]:
                if record.get(key) != expected:
                    raise ValueError(f'Unexpected {key}: {path}')
            if not all(Path(p).is_file() for p in record['restarts']):
                raise ValueError(f'Missing accepted restart: {path}')
            cursor = stop
        if cursor != end:
            raise ValueError('Campaign endpoint differs')
        dates = {(first + timedelta(days=i)).strftime('%Y%m%d') for i in range((end-first).days)}
        for kind, suffix in [('daily', '.LDASOUT_DOMAIN1.daily'),
                             ('daily', '.CHRTOUT_DOMAIN1.daily'), ('hourly', '.CHRTOUT_DOMAIN1')]:
            paths = sorted((output / CAMPAIGN / kind).glob('*/*/*' + suffix))
            expected = dates | {end.strftime('%Y%m%d')} if kind == 'hourly' else dates
            if {p.name[:8] for p in paths} != expected or len(paths) != len(expected):
                raise ValueError(f'Coverage mismatch: {kind} {suffix}')
        records = 0
        archives = sorted((output / CAMPAIGN / 'hourly').glob('*/*/*.archive.json'))
        if len(archives) != len(dates) + 1:
            raise ValueError('Missing hourly archive receipts')
        for path in archives:
            record = json.loads(path.read_text())
            archive = path.with_name(path.name.removesuffix('.archive.json'))
            if (record.get('status') != 'passed' or not record.get('all_variable_values_verified')
                    or record.get('published_identity') != identity(archive)):
                raise ValueError(f'Invalid hourly archive acceptance: {path}')
            records += record['records']
            expected_records = (23 if archive.name.startswith(first.strftime('%Y%m%d')) else
                                1 if archive.name.startswith(end.strftime('%Y%m%d')) else 24)
            if record['records'] != expected_records:
                raise ValueError(f'Unexpected archive record count: {path}')
        if records != len(dates) * 24:
            raise ValueError(f'Hourly record total differs: {records}')
        files = []
        for source, target in moves:
            for path in source.rglob('*'):
                if path.is_symlink():
                    raise ValueError(f'Unexpected symlink: {path}')
                if path.is_file():
                    files.append({'old': str(path), 'new': str(target / path.relative_to(source)),
                                  'identity': identity(path)})
        report = {'status': 'audited', 'campaign': CAMPAIGN, 'segments': len(accepted),
                  'days': len(dates), 'hourly_records': records, 'file_count': len(files),
                  'moves': [[str(a), str(b)] for a, b in moves], 'files': files}
        _atomic_json(state / 'plan.json', report)
        print(json.dumps({k: v for k, v in report.items() if k not in ('files', 'moves')}), flush=True)
        if not args.execute:
            return
        report = {'status': 'moving', 'campaign': CAMPAIGN, 'completed_moves': [],
                  'metadata_updated': [], 'plan': str(state / 'plan.json')}
        _atomic_json(marker, report)
        for source, target in moves:
            source.rename(target)
            report['completed_moves'].append([str(source), str(target)])
            _atomic_json(marker, report)
        for record in files:
            if identity(Path(record['new'])) != record['identity']:
                raise ValueError(f'File identity changed: {record["new"]}')
        # Preserve originals of every rewritten operational JSON. Logs and
        # historical test reports remain immutable historical evidence.
        mapping = {str(output / CAMPAIGN) + '/': str(output) + '/',
                   str(restart / CAMPAIGN) + '/': str(restart) + '/'}
        for path in sorted(runs.rglob('*.json')):
            original = path.read_text()
            updated = original
            for old, new in mapping.items():
                updated = updated.replace(old, new)
            if updated == original:
                continue
            json.loads(updated)
            backup = state / 'metadata-before' / path.relative_to(runs)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(original)
            _atomic_json(path, json.loads(updated))
            report['metadata_updated'].append(str(path))
            _atomic_json(marker, report)
        report.update(status='complete', outputs=str(output), restarts=str(restart),
                      file_count=len(files), days=len(dates), hourly_records=records)
        _atomic_json(marker, report)
        print(json.dumps({k: v for k, v in report.items() if k not in ('completed_moves', 'metadata_updated')}))


if __name__ == '__main__':
    main()
