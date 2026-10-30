"""CNRFC midnight replay planning and recoverable publication (no model launch)."""
# Model clocks deliberately use timezone-naive UTC.
# ruff: noqa: DTZ007
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from hydro_ops.wrf_hydro.channel_archive import identity, publish_day
from hydro_ops.wrf_hydro.production import restart_names, write_json


def choose_start(end, checkpoints, rewind_days=11, published_end=None):
    if rewind_days < 1 or end.hour or end.minute or end.second:
        raise ValueError('Require positive rewind and a midnight endpoint')
    desired = end - timedelta(days=rewind_days)
    if published_end is not None:
        if end < published_end:
            raise ValueError('Cannot shorten the published simulation')
        desired = min(desired, published_end)
    available = [stamp for stamp in checkpoints if stamp <= desired]
    if not available:
        raise ValueError('No checkpoint at or before the required rewind')
    return max(available)


def checkpoints(root):
    found = {}
    for path in sorted(root.glob('*/*/RESTART.*_DOMAIN1')):
        stamp = datetime.strptime(path.name, 'RESTART.%Y%m%d%H_DOMAIN1')
        pair = [path.parent / n for n in restart_names(stamp)]
        if stamp.hour == 0 and all(p.is_file() for p in pair):
            found[stamp] = path.parent
    return found


def discover_plan(root, end=None, rewind_days=11, domain='cnrfc'):
    if domain not in ('cnrfc', 'b120', 'yampa'):
        raise ValueError('Unsupported daily model domain')
    forcing = root / f'forcing/outputs/{domain}/nrt/hourly'
    paths = sorted(forcing.glob('*/*/*.LDASIN_DOMAIN1'))
    if not paths:
        raise ValueError('No CNRFC NRT forcing')
    latest = datetime.strptime(paths[-1].name[:8], '%Y%m%d')
    end = latest if end is None else end
    if end > latest:
        raise ValueError('Requested endpoint exceeds forcing coverage')
    state = root / f'nwm/status/{domain}/nrt-daily/latest.json'
    published_end = None
    if state.exists():
        record = json.loads(state.read_text())
        if record['status'] != 'complete':
            raise ValueError('Last publication is incomplete; inspect transaction journal')
        published_end = datetime.fromisoformat(record['end'])
    else:
        prior = root / f'nwm/runs/{domain}/nrt/nrt_continuation_v1'
        accepted = [json.loads(p.read_text()) for p in prior.glob('*-passed.json')]
        published_end = max((datetime.fromisoformat(r['end']) for r in accepted if r.get('passed')), default=None)
    available = checkpoints(root / f'nwm/restarts/{domain}/nrt')
    start = choose_start(end, available, rewind_days, published_end)
    return {'start': str(start), 'end': str(end), 'desired_start': str(end-timedelta(days=rewind_days)),
            'initial_restart_dir': str(available[start]), 'rewind_days': rewind_days,
            'days': (end-start).days, 'previous_end': str(published_end) if published_end else None}


def retention_candidates(restart_root, end, keep_days=30):
    """Paired checkpoints only; monthly anchors always retained. Planning only."""
    cutoff = end - timedelta(days=keep_days)
    return [str(folder / name) for stamp, folder in checkpoints(restart_root).items()
            if stamp < cutoff and stamp.day != 1 for name in restart_names(stamp)]


def prepare_publication(workspace, output_root, restart_root):
    """Merge boundary records in staging and return validated candidate/destination pairs."""
    pairs = []
    for source in sorted((workspace / 'outputs/hourly').glob('*/*/*.CHRTOUT_DOMAIN1')):
        relative = source.relative_to(workspace / 'outputs')
        target = output_root / relative
        if target.exists():
            receipt = json.loads(target.with_name(target.name + '.archive.json').read_text())
            if (receipt.get('status') != 'passed' or not receipt.get('all_variable_values_verified')
                    or receipt.get('published_identity') != identity(target)):
                raise ValueError(f'Stale existing channel archive: {target}')
            merged = workspace / 'merged' / relative
            publish_day([target, source], merged, replace_overlap=True)
            source = merged
        receipt_path = source.with_name(source.name + '.archive.json')
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('published_identity') != identity(source) or not receipt.get('all_variable_values_verified'):
            raise ValueError(f'Invalid candidate archive: {source}')
        pairs += [(source, target), (receipt_path, target.with_name(target.name + '.archive.json'))]
    pairs += [(p, output_root / p.relative_to(workspace / 'outputs'))
              for p in sorted((workspace / 'outputs/daily').glob('*/*/*.daily'))]
    pairs += [(p, restart_root / p.relative_to(workspace / 'restarts'))
              for p in sorted((workspace / 'restarts').glob('*/*/*')) if p.is_file()]
    if not pairs:
        raise ValueError('No staged publication')
    return pairs


def replace_link(source, target):
    """Atomic per-file activation without copying data; same filesystem required."""
    temporary = target.with_name(target.name + f'.cycle-{os.getpid()}.partial')
    try:
        os.link(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def publish_transaction(pairs, journal, *, activate=replace_link):
    """Back up every old file before activation; roll back ordinary failures.

    Abrupt process/node loss leaves a publishing journal and blocks subsequent
    cycles. Multi-file visibility is not atomic; readers must honor the gate.
    """
    if journal.exists():
        raise ValueError('Transaction journal already exists; do not blindly retry')
    journal.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if len({str(target) for _, target in pairs}) != len(pairs):
        raise ValueError('Duplicate publication target')
    for i, (source, target) in enumerate(pairs):
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink() or target.is_symlink() or source.stat().st_dev != target.parent.stat().st_dev:
            raise ValueError('Require regular same-filesystem publication')
        backup = journal.parent / 'previous' / str(i)
        before = identity(target) if target.exists() else None
        if before is not None:
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.link(target, backup)
        rows.append({'source': str(source), 'target': str(target), 'before': before,
                     'after': identity(source), 'backup': str(backup) if before else None})
    record = {'status': 'prepared', 'files': rows}
    write_json(journal, record)
    try:
        record['status'] = 'publishing'
        write_json(journal, record)
        for row in rows:
            target = Path(row['target'])
            if (identity(target) if target.exists() else None) != row['before']:
                raise ValueError(f'Destination changed: {target}')
            if identity(Path(row['source'])) != row['after']:
                raise ValueError('Candidate changed')
            activate(Path(row['source']), target)
        if any(identity(Path(r['target'])) != r['after'] for r in rows):
            raise ValueError('Post-publication identity mismatch')
    except BaseException:
        # Roll back only files that this transaction actually replaced.
        for row in reversed(rows):
            target = Path(row['target'])
            if target.exists() and identity(target) == row['after']:
                if row['backup']:
                    replace_link(Path(row['backup']), target)
                else:
                    target.unlink()
        record['status'] = 'rolled_back'
        write_json(journal, record)
        raise
    record['status'] = 'complete'
    write_json(journal, record)
    return record
