"""Reuse an accepted staged prefix and pin atomically published forcing inputs."""
# Model clocks use timezone-naive UTC.
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from hydro_ops.wrf_hydro.production import restart_names, write_json


def file_identity(path):
    info = path.stat()
    return [info.st_ino, info.st_size, info.st_mtime_ns]


def reuse_prefix(source, destination, start, end):
    """Hardlink accepted data only; leave failed workspace and its reports intact."""
    prior = json.loads((source / 'cycle.json').read_text())
    if (prior.get('status') != 'failed' or prior.get('rst_typ') != 1
            or prior.get('test') or not prior.get('historical_nrt_refresh')
            or prior.get('start') != str(start) or prior.get('end') != str(end)
            or (source / 'publication/transaction.json').exists()):
        raise ValueError('Not an unpublished failed option-1 refresh with matching interval')
    cursor, records = start, []
    for marker in sorted((source / 'runs').glob('*/*/accepted.json')):
        record = json.loads(marker.read_text())
        stop = datetime.fromisoformat(record['end'])
        if (record.get('start') != str(cursor) or not cursor < stop <= end
                or not record.get('passed') or record.get('rst_typ') != 1):
            raise ValueError('Accepted prefix has a gap or invalid segment')
        if not record.get('forcing_identities') or any(
                file_identity(Path(p)) != identity
                for p, identity in record['forcing_identities'].items()):
            raise ValueError('Accepted prefix forcing changed; cannot reuse')
        records.append((marker, record))
        cursor = stop
    if not records or cursor >= end:
        raise ValueError('Require a nonempty accepted prefix and an unfinished tail')
    # Only expected filenames are selected, never unaccepted tail products.
    paths = []
    day = start
    while day < cursor:
        for product in ('LDASOUT', 'CHRTOUT'):
            paths.append(Path('outputs/daily') / day.strftime(
                f'%Y/%m/%Y%m%d.{product}_DOMAIN1.daily'))
        day += timedelta(days=1)
    day = start
    while day <= cursor:
        path = Path('outputs/hourly') / day.strftime('%Y/%m/%Y%m%d.CHRTOUT_DOMAIN1')
        paths.extend([path, path.with_name(path.name + '.archive.json')])
        if day > start:
            paths.extend(Path('restarts') / day.strftime('%Y/%m') / n for n in restart_names(day))
        day += timedelta(days=1)
    if any(not (source / p).is_file() or (source / p).is_symlink() for p in paths):
        raise ValueError('Accepted prefix has missing/nonregular staged products')
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source / relative, target)
    for marker, record in records:
        record = record | {'reused_from': str(marker)}
        write_json(destination / marker.relative_to(source), record)
    return cursor, {'source_workspace': str(source), 'segments': len(records),
                    'days': (cursor-start).days, 'files': len(paths), 'resume_at': str(cursor)}


def snapshot_forcing(source, destination, start, end):
    """Pin data inodes; the CNRFC subset writer publishes with atomic replace.

    Receipts are captured and validated against each pinned inode. Source changes
    during capture abort; subsequent atomic replacements cannot change the pin.
    The runner still verifies snapshot identities before/after simulation.
    """
    records = {}
    day = start
    while day <= end:
        relative = Path(day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1'))
        original, target = source / relative, destination / relative
        before = file_identity(original)
        receipt = json.loads(original.with_name(original.name + '.subset.json').read_text())
        if (receipt.get('status') != 'passed' or receipt.get('missing_active_values') != 0
                or receipt.get('output_identity') != before):
            raise ValueError(f'Stale or invalid forcing receipt: {original}')
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(original, target)
        if file_identity(target) != before or file_identity(original) != before:
            raise ValueError('Forcing changed during snapshot capture; retry')
        write_json(target.with_name(target.name + '.subset.json'), receipt)
        records[str(original)] = before
        day += timedelta(days=1)
    if any(file_identity(Path(p)) != v for p, v in records.items()):
        raise ValueError('Forcing changed during snapshot capture; retry')
    write_json(destination / 'snapshot.json', {'source_identities': records,
               'policy': 'pinned_atomic_publication_inodes', 'start': str(start), 'end': str(end)})
    return records
