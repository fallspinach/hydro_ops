"""CDEC sensor-3/82 SWE history, preserving native sampling and source flags."""
from __future__ import annotations

import csv
import fcntl
import gzip
import hashlib
import io
import json
import math
import time
from datetime import UTC, date, datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.download.cdec_fnf import ENDPOINT, PST, atomic_bytes, write_json

DURATIONS = {'H': 'hourly', 'E': 'event', 'D': 'daily', 'M': 'monthly'}
FIELDS = ['station_id', 'sensor_number', 'duration_code', 'provider_date', 'provider_obs_date',
          'timestamp_pst', 'timestamp_utc', 'observation_timestamp_pst', 'observation_timestamp_utc',
          'raw_value', 'value_inches', 'swe_mm', 'data_flag', 'value_status']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def series(metadata):
    if metadata.get('status') != 'passed' or metadata.get('failures'):
        raise ValueError('SWE station metadata must pass')
    result, seen = [], set()
    for station in metadata['stations']:
        for sensor in station['sensors']:
            if sensor['sensor_number'] not in (3, 82):
                continue
            code = sensor['duration_code']
            if code not in DURATIONS or sensor['units'] != 'INCHES':
                raise ValueError('Unknown SWE duration or units')
            key = (station['station_id'], sensor['sensor_number'], code)
            if key in seen:
                raise ValueError('Duplicate metadata series')
            seen.add(key)
            begin, separator, finish = sensor['advertised_availability'].partition('to ')
            if not separator:
                raise ValueError('Unrecognized SWE availability metadata')
            begin = begin.strip()
            start_source = 'provider_metadata' if begin else 'explicit_request_floor_missing_provider_start'
            begin = (datetime.strptime(begin, '%m/%d/%Y').replace(tzinfo=PST).date()
                     if begin else date(1800, 1, 1))
            finish = (None if finish == 'present' else
                      datetime.strptime(finish, '%m/%d/%Y').replace(tzinfo=PST).date().isoformat())
            result.append({'station_id': key[0], 'sensor_number': key[1], 'duration_code': code,
                           'resolution': DURATIONS[code], 'start': begin.isoformat(),
                           'advertised_end': finish, 'start_source': start_source})
    return sorted(result, key=lambda r: (r['sensor_number'], r['resolution'], r['station_id']))


def directory(root, spec):
    return (root / 'observations/processed/cdec/swe' / f'sensor_{spec["sensor_number"]}'
            / spec['resolution'] / spec['station_id'])


def tasks_for(root, specifications, today, mode):
    tasks = []
    for spec in specifications:
        begin = date.fromisoformat(spec['start'])
        end = min(today, date.fromisoformat(spec['advertised_end'])) if spec['advertised_end'] else today
        if mode == 'smoke':
            begin = max(begin, end - timedelta(days=365 if spec['duration_code'] == 'M' else 2))
        elif mode == 'update' and spec['duration_code'] != 'M':
            cursor = directory(root, spec) / 'cursor.json'
            if not cursor.exists():
                raise ValueError('Initial SWE backfill must finish before routine updates')
            checked = date.fromisoformat(json.loads(cursor.read_text())['checked_through'])
            lookback = 400 if spec['duration_code'] == 'D' else 7
            begin = max(begin, min(today - timedelta(days=lookback), checked + timedelta(days=1)))
        if begin > end:
            continue
        # Hourly/event requests are bounded to one calendar year to avoid huge responses.
        while begin <= end:
            stop = min(end, date(begin.year, 12, 31)) if spec['duration_code'] in ('H', 'E') else end
            task = spec | {'request_start': begin.isoformat(), 'request_end': stop.isoformat()}
            task['request_id'] = digest(json.dumps(task, sort_keys=True).encode())
            tasks.append(task)
            begin = stop + timedelta(days=1)
    if mode == 'smoke':
        groups = {}
        for task in tasks:
            groups.setdefault((task['sensor_number'], task['duration_code']), task)
        tasks = list(groups.values())
    return sorted(tasks, key=lambda t: (t['duration_code'] in ('H', 'E'),
                                       -date.fromisoformat(t['request_end']).toordinal(), t['request_id']))


def normalize(rows, task):
    result = {}
    start = date.fromisoformat(task['request_start'])
    end = date.fromisoformat(task['request_end'])
    for row in rows:
        if (row['stationId'] != task['station_id'] or row['SENSOR_NUM'] != task['sensor_number']
                or row['durCode'] != task['duration_code'] or row['units'] != 'INCHES'):
            raise ValueError('SWE response identity/units mismatch')
        stamp = datetime.strptime(row['date'], '%Y-%m-%d %H:%M').replace(tzinfo=PST)
        if not start <= stamp.date() <= end:
            # CDEC's date-only request bounds can shift by one hour in summer,
            # despite fixed-PST output. Request padding and crop source labels.
            if not start - timedelta(days=2) <= stamp.date() <= end + timedelta(days=2):
                raise ValueError('Observation outside padded request interval')
            continue
        obs_date = row.get('obsDate')
        observed = datetime.strptime(obs_date, '%Y-%m-%d %H:%M').replace(tzinfo=PST) if obs_date else None
        raw = row.get('value')
        try:
            value = float(raw)
        except (ValueError, TypeError):
            value = None
        if value is None or not math.isfinite(value):
            value, state = None, 'missing_or_nonnumeric'
        elif value == -9999:
            value, state = None, 'missing_sentinel_-9999'
        else:
            state = 'negative_swe' if value < 0 else 'numeric'
        record = {'station_id': task['station_id'], 'sensor_number': task['sensor_number'],
                  'duration_code': task['duration_code'], 'provider_date': row['date'],
                  'provider_obs_date': obs_date, 'timestamp_pst': stamp.isoformat(),
                  'timestamp_utc': stamp.astimezone(UTC).isoformat(),
                  'observation_timestamp_pst': observed.isoformat() if observed else None,
                  'observation_timestamp_utc': observed.astimezone(UTC).isoformat() if observed else None,
                  'raw_value': raw, 'value_inches': value,
                  'swe_mm': value * 25.4 if value is not None else None,
                  'data_flag': row.get('dataFlag'), 'value_status': state}
        key = record['timestamp_pst']
        if key in result and result[key] != record:
            raise ValueError('Conflicting duplicate SWE timestamp; retain raw response for review')
        result[key] = record
    return [result[k] for k in sorted(result)]


def csv_bytes(records):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader(); writer.writerows(records)
    return stream.getvalue().encode()


def canonical(records):
    return list(csv.DictReader(io.StringIO(csv_bytes(records).decode(), newline='')))


def identity(path):
    stat = path.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def acquire(arguments):
    root, task, refresh = arguments
    output = directory(root, task)
    output.mkdir(parents=True, exist_ok=True)
    receipt = (root / 'observations/status/cdec/swe/chunks' / task['station_id']
               / str(task['sensor_number']) / task['duration_code'] / (task['request_id'] + '.json'))
    with (output / '.write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if receipt.exists() and not refresh:
            previous = json.loads(receipt.read_text())
            if previous.get('status') == 'passed' and (root / previous['raw_file']).is_file() and all(
                    (root / p).exists() and identity(root / p) == ident
                    for p, ident in previous['output_identities'].items()):
                return {'status': 'skipped', 'request_id': task['request_id'], 'receipt': str(receipt.relative_to(root))}
        params = {'Stations': task['station_id'], 'SensorNums': task['sensor_number'],
                  'dur_code': task['duration_code'],
                  'Start': (date.fromisoformat(task['request_start']) - timedelta(days=1)).isoformat(),
                  'End': (date.fromisoformat(task['request_end']) + timedelta(days=2)).isoformat()}
        with requests.Session() as session:
            session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1,
                status_forcelist=(429, 500, 502, 503, 504))))
            response = session.get(ENDPOINT, params=params, timeout=(20, 180))
            response.raise_for_status()
            payload = response.content
            checksum = digest(payload)
            raw = (root / 'observations/inputs/cdec/swe' / f'sensor_{task["sensor_number"]}'
                   / task['resolution'] / task['station_id'] / (checksum + '.json.gz'))
            atomic_bytes(raw, gzip.compress(payload, compresslevel=4, mtime=0))
            if digest(gzip.decompress(raw.read_bytes())) != checksum:
                raise ValueError('Retained raw SWE response checksum mismatch')
            rows = response.json()
            if not isinstance(rows, list):
                raise TypeError('Expected CDEC JSON array, including explicit [] for an empty interval')
        time.sleep(.2)
        records = normalize(rows, task)
        converted = canonical(records)
        by_year = {}
        monthly = task['duration_code'] == 'M'
        for row in converted:
            by_year.setdefault('history' if monthly else row['timestamp_pst'][:4], []).append(row)
        begin, end = date.fromisoformat(task['request_start']), date.fromisoformat(task['request_end'])
        # Include old years even for an empty response, enforcing the history-loss guard.
        years = ({'history'} if monthly and (output / 'history.csv.gz').exists() else set()) if monthly else {
            p.stem[:4] for p in output.glob('????.csv.gz') if begin.year <= int(p.stem[:4]) <= end.year}
        publications, changed = {}, 0
        for year in sorted(years | set(by_year)):
            target = output / (year + '.csv.gz')
            old = (list(csv.DictReader(io.StringIO(gzip.decompress(target.read_bytes()).decode(), newline='')))
                   if target.exists() else [])
            incoming = {r['timestamp_pst']: r for r in by_year.get(year, [])}
            selected_old = {r['timestamp_pst'] for r in old
                            if task['request_start'] <= r['timestamp_pst'][:10] <= task['request_end']}
            if selected_old - set(incoming):
                raise ValueError('Previously published SWE timestamps omitted by server; review raw response')
            merged = {r['timestamp_pst']: r for r in old} | incoming
            contents = csv_bytes([merged[k] for k in sorted(merged)])
            changed += atomic_bytes(target, gzip.compress(contents, compresslevel=4, mtime=0))
            # Reopen published data and compare each requested source row exactly.
            decoded = gzip.decompress(target.read_bytes())
            if decoded != contents:
                raise ValueError('Published SWE CSV transfer/serialization mismatch')
            actual = {r['timestamp_pst']: r for r in csv.DictReader(io.StringIO(decoded.decode(), newline=''))}
            if any(actual[k] != v for k,v in incoming.items()):
                raise ValueError('Published SWE values differ from normalized source')
            publications[str(target.relative_to(root))] = identity(target)
        numeric = [r for r in records if r['value_inches'] is not None]
        result = task | {'status': 'passed', 'request_url': response.url,
                         'retrieved_utc': datetime.now(UTC).isoformat(), 'raw_file': str(raw.relative_to(root)),
                         'source_sha256': checksum, 'rows': len(records), 'numeric_rows': len(numeric),
                         'missing_rows': len(records) - len(numeric),
                         'negative_rows': sum(r['value_inches'] < 0 for r in numeric),
                         'first_timestamp_pst': records[0]['timestamp_pst'] if records else None,
                         'latest_timestamp_pst': records[-1]['timestamp_pst'] if records else None,
                         'latest_numeric_timestamp_pst': numeric[-1]['timestamp_pst'] if numeric else None,
                         'missing_observation_dates': sum(not r['provider_obs_date'] for r in records),
                         'output_identities': publications, 'files_changed': changed,
                         'source_to_published_rows_verified': True,
                         'coverage_semantics': 'Sparse and all-missing intervals are preserved; no regular sampling inferred'}
        write_json(receipt, result)
        cursor = output / 'cursor.json'
        checked = json.loads(cursor.read_text())['checked_through'] if cursor.exists() else task['request_end']
        write_json(cursor, {'checked_through': max(checked, task['request_end']), 'last_receipt': str(receipt.relative_to(root))})
        return {'status': 'passed', 'request_id': task['request_id'], 'receipt': str(receipt.relative_to(root)),
                'rows': len(records), 'numeric_rows': len(numeric), 'changed': changed}


def safe_acquire(arguments):
    try:
        return acquire(arguments)
    except (requests.RequestException, ValueError, OSError, KeyError, TypeError) as error:
        return {'status': 'failed', 'request_id': arguments[1]['request_id'],
                'task': arguments[1], 'error': str(error)}
