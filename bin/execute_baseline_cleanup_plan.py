"""Execute an approved explicit baseline plan with identity checks and a durable journal."""
import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from plan_baseline_cleanup import evidence, identity


def validate_entry(entry, root):
    day = date.fromisoformat(entry['day'])
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    base = root/'forcing/outputs/conus/baseline'/relative
    expected = [base, base.with_name(base.name+'.manifest.json')]
    if entry['status'] != 'eligible' or not entry['checks'] or not all(entry['checks'].values()):
        raise ValueError(f'Unaccepted entry: {day}')
    if [p['path'] for p in entry['delete']] != [str(p) for p in expected]:
        raise ValueError(f'Unexpected deletion targets: {day}')
    replacement = root/'forcing/outputs/conus/retro'/relative
    if entry['replacement']['path'] != str(replacement):
        raise ValueError(f'Unexpected replacement: {day}')
    for item in [*entry['delete'], entry['replacement'], *entry['evidence']]:
        path = Path(item['path'])
        if not path.is_file() or path.resolve() != path or identity(path) != item['identity']:
            raise ValueError(f'Changed, missing, or symlink file: {path}')
    for item in entry['evidence']:
        _, actual = evidence(Path(item['path']))
        if actual != item:
            raise ValueError(f'Changed audit evidence: {item["path"]}')
    return entry['day']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    raw = args.plan.read_bytes()
    plan = json.loads(raw)
    entries = plan['entries']
    if (plan['status'] != 'passed' or plan['blocked_days'] != 0
            or plan['mode'] != 'plan_only_no_deletion' or plan['eligible_days'] != len(entries)):
        raise ValueError('Plan is not fully accepted')
    start, end = date.fromisoformat(plan['start']), date.fromisoformat(plan['end'])
    expected_days = [str(start+timedelta(days=i)) for i in range((end-start).days+1)]
    if [e['day'] for e in entries] != expected_days:
        raise ValueError('Plan dates are incomplete, duplicated, or out of order')
    if set(expected_days) & set(plan['retained_boundary_days']):
        raise ValueError('Plan includes retained boundary days')
    with ThreadPoolExecutor(max_workers=8) as pool:
        for n, _ in enumerate(pool.map(lambda e: validate_entry(e, root), entries), 1):
            if n % 500 == 0:
                print(json.dumps({'preflight_checked': n}), flush=True)
    print(json.dumps({'preflight': 'passed', 'days': len(entries)}), flush=True)
    if not args.execute:
        return
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    with args.journal.open('x') as journal:
        def log(record):
            journal.write(json.dumps(record)+'\n')
            journal.flush()
            os.fsync(journal.fileno())
        log({'event': 'start', 'plan': str(args.plan.resolve()),
             'plan_sha256': hashlib.sha256(raw).hexdigest(),
             'created': datetime.now(UTC).isoformat()})
        removed = 0
        size = 0
        for entry in entries:
            validate_entry(entry, root)
            log({'event': 'delete_intent', 'day': entry['day'], 'files': entry['delete']})
            for item in entry['delete']:
                path = Path(item['path'])
                if identity(path) != item['identity']:
                    raise ValueError(f'Target changed before deletion: {path}')
                path.unlink()
                removed += 1
                size += item['identity']['bytes']
            log({'event': 'deleted', 'day': entry['day']})
            if removed % 1000 == 0:
                print(json.dumps({'deleted_files': removed, 'bytes': size}), flush=True)
        remaining = [item['path'] for e in entries for item in e['delete'] if Path(item['path']).exists()]
        if remaining:
            raise RuntimeError(f'Unexpected remaining targets: {remaining[:5]}')
        result = {'event': 'completed', 'deleted_days': len(entries),
                  'deleted_files': removed, 'bytes': size, 'remaining_targets': 0,
                  'finished': datetime.now(UTC).isoformat()}
        log(result)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
