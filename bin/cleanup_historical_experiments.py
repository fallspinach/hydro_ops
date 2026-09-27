#!/usr/bin/env python3
"""Plan or execute a narrowly scoped, identity-checked historical-array cleanup.

Only the nine manually reviewed experiment roots below are eligible. Reports,
unknown file types, symlinks, hard links and restart/input material are retained.
This is not a general work-directory cleaner.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOTS = (
    'forcing/work/nrt-schema-v1-4626557',
    'forcing/work/nrt-schema-v1-4626634',
    'forcing/work/nrt-efficiency-4629922',
    'forcing/work/post2020-stage-profile',
    'forcing/work/precipitation-multiday-benchmark',
    'forcing/work/chunk-calendar-benchmark',
    'forcing/work/retro-prism-optimization-20260915T015927',
    'nwm/outputs/tests/mid_atlantic/nrt_20260310_48h',
    'nwm/outputs/tests/mid_atlantic/monthly_prism',
)
REPORT_SUFFIXES = {'.json', '.jsonl', '.md', '.txt', '.log', '.out', '.pstats'}


def identity(path):
    s = path.lstat()
    return {'device': s.st_dev, 'inode': s.st_ino, 'bytes': s.st_size,
                'mtime_ns': s.st_mtime_ns, 'links': s.st_nlink, 'mode': s.st_mode}


def eligible(root, path):
    relative = path.relative_to(root)
    if not any(relative.is_relative_to(Path(base)) for base in ROOTS):
        return False
    if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
        return False
    if path.stat().st_nlink != 1:
        return False
    if any('restart' in p.lower() or p.lower() in {'inputs', 'domain', 'recovery'}
           for p in relative.parts):
        return False
    return path.suffix == '.nc' or bool(re.fullmatch(
        r'\d{8,12}\.(?:LDASIN|LDASOUT|CHRTOUT)_DOMAIN1(?:\.daily)?', path.name))


def scheduler_snapshot():
    result = subprocess.check_output(
        ['squeue', '--me', '--noheader', '--format=%i|%j|%T|%o|%Z'], text=True)
    # Fail closed for any experiment-like live job, not only an exact path match.
    for line in result.splitlines():
        name = line.split('|')[1].lower()
        if (any(word in name for word in ('benchmark', 'pilot', 'profile', 'schema', 'test'))
                or any(Path(base).name in line for base in ROOTS)):
            raise RuntimeError(f'Potential live experiment consumer: {line}')
    return result


def inventory(root):
    deleted, retained = [], []
    for base in ROOTS:
        directory = root / base
        if not directory.is_dir() or directory.resolve() != directory:
            raise ValueError(f'Missing or redirected experiment root: {directory}')
        for parent, dirs, files in os.walk(directory, followlinks=False):
            # Do not traverse symlink directories or follow them to production data.
            dirs[:] = sorted(d for d in dirs if not (Path(parent) / d).is_symlink())
            for name in sorted(files):
                path = Path(parent) / name
                item = {'path': str(path.relative_to(root)), 'identity': identity(path)}
                if eligible(root, path):
                    deleted.append(item)
                else:
                    if (path.resolve() == path and stat.S_ISREG(path.lstat().st_mode)
                            and path.suffix in REPORT_SUFFIXES):
                        item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                        if identity(path) != item['identity']:
                            raise ValueError(f'Report changed: {path}')
                    retained.append(item)
    return deleted, retained


def durable_json(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as out:
        json.dump(record, out, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())


def check_entry(root, item):
    path = root / item['path']
    if (not eligible(root, path) or identity(path) != item['identity']):
        raise ValueError(f'Changed or ineligible deletion target: {path}')


def check_reports(root, retained):
    for item in retained:
        path = root / item['path']
        if identity(path) != item['identity']:
            raise ValueError(f'Retained file changed: {path}')
        if 'sha256' in item and hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'Retained report changed: {path}')


def execute(root, plan, journal_path, snapshot):
    if plan['project_root'] != str(root) or plan['reviewed_roots'] != list(ROOTS):
        raise ValueError('Wrong project or cleanup scope')
    if plan['mode'] != 'reviewed_experimental_arrays_only':
        raise ValueError('Wrong cleanup mode')
    entries = plan['delete']
    if len({e['path'] for e in entries}) != len(entries):
        raise ValueError('Duplicate deletion entry')
    for item in entries:
        check_entry(root, item)
    check_reports(root, plan['retain'])
    with journal_path.open('x') as journal:
        def log(record):
            journal.write(json.dumps(record) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
        log({'event': 'start', 'created': datetime.now(UTC).isoformat(),
                 'plan_sha256': hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
                 'scheduler': snapshot})
        for item in entries:
            check_entry(root, item)
            log(dict(event='delete_intent', **item))
            (root / item['path']).unlink()
            log({'event': 'deleted', 'path': item['path']})
        check_reports(root, plan['retain'])
        result = {'event': 'completed', 'deleted_files': len(entries),
                      'logical_bytes': sum(e['identity']['bytes'] for e in entries),
                      'retained_files': len(plan['retain']), 'retained_reports_verified': True,
                      'finished': datetime.now(UTC).isoformat()}
        log(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    snapshot = scheduler_snapshot()
    if args.execute:
        result = execute(root, json.loads(args.plan.read_text()),
                         args.plan.with_suffix('.journal.jsonl'), snapshot)
    else:
        deleted, retained = inventory(root)
        plan = {'mode': 'reviewed_experimental_arrays_only', 'project_root': str(root),
                    'created': datetime.now(UTC).isoformat(), 'reviewed_roots': list(ROOTS),
                    'scheduler': snapshot, 'delete': deleted, 'retain': retained,
                    'dependency_review': 'Code references reviewed: matching runtime references '
                    'are experiment output writers, not operational readers. Active production '
                    'entry points do not consume these nine roots. Other work trees excluded.'}
        durable_json(args.plan, plan)
        result = {'planned_files': len(deleted), 'logical_bytes': sum(e['identity']['bytes'] for e in deleted),
                      'retained_files': len(retained), 'plan': str(args.plan)}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
