"""CDEC full natural flow acquisition and lossless, flagged CSV normalization."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import tempfile
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ENDPOINT = 'https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet'
PST = timezone(timedelta(hours=-8), 'PST')
PRODUCTS = {'daily': (8, 'D', 'CFS', 0.028316846592, 'm3 s-1'),
            'monthly': (65, 'M', 'AF', 1233.48183754752, 'm3')}


def atomic_bytes(path: Path, content: bytes) -> bool:
    """Publish one complete file; avoid rewriting unchanged records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return False
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', delete=False) as handle:
            staging = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return True


def write_json(path: Path, content: dict) -> None:
    atomic_bytes(path, (json.dumps(content, indent=2, allow_nan=False) + '\n').encode())


def series_from_metadata(report: dict, station_ids: list[str]) -> list[dict]:
    if report.get('status') != 'passed':
        raise ValueError('Station metadata inventory did not pass')
    stations = {r['station_id']: r for r in report['stations']}
    if set(stations) != set(station_ids) or len(stations) != len(report['stations']):
        raise ValueError('Metadata station set differs from configuration')
    series = []
    for station in station_ids:
        for resolution, (sensor, duration, units, _, _) in PRODUCTS.items():
            selected = [s for s in stations[station]['sensors']
                        if s['sensor_number'] == sensor and s['duration_code'] == duration]
            if not selected:
                if (station, resolution) == ('TNL', 'daily'):
                    continue
                raise ValueError(f'Missing FNF sensor {station}/{resolution}')
            if len(selected) != 1 or selected[0]['units'] != units:
                raise ValueError('Duplicate FNF sensor or unexpected units')
            # Blank Start silently defaults to a short window; always send a date.
            begin = datetime.strptime(selected[0]['advertised_availability'].split(' to ')[0], '%m/%d/%Y').replace(tzinfo=PST).date()
            series.append({'station_id': station, 'resolution': resolution,
                           'sensor_number': sensor, 'duration_code': duration,
                           'start': begin.isoformat(), 'metadata_url': stations[station]['source_url']})
    return series


def normalize(rows: list, spec: dict, end: date) -> list[dict]:
    sensor, duration, units, factor, si_units = PRODUCTS[spec['resolution']]
    records = {}
    for row in rows:
        if (row['stationId'] != spec['station_id'] or row['SENSOR_NUM'] != sensor
                or row['durCode'] != duration or row['units'] != units):
            raise ValueError('Response station/sensor/duration/units mismatch')
        stamp = datetime.strptime(row['date'], '%Y-%m-%d %H:%M').replace(tzinfo=PST)
        obs_date = row.get('obsDate')
        obs_stamp = (datetime.strptime(obs_date, '%Y-%m-%d %H:%M').replace(tzinfo=PST)
                     if obs_date else None)
        if not date.fromisoformat(spec['start']) <= stamp.date() <= end:
            raise ValueError('Response date outside explicit request')
        raw = row['value']
        if raw is None or str(raw).strip().upper() in ('', 'M', '---', 'NAN', 'NULL'):
            value, state = None, 'missing'
        else:
            try:
                value = float(raw)
            except (ValueError, TypeError):
                value = None
            if value is None or not math.isfinite(value):
                value, state = None, 'nonnumeric'
            elif value == -9999:
                value, state = None, 'missing_sentinel_-9999'
            else:
                state = 'negative' if value < 0 else 'numeric'
        record = {'station_id': spec['station_id'], 'sensor_number': sensor,
                  'duration_code': duration, 'period_label': stamp.strftime('%Y-%m-%d' if duration == 'D' else '%Y-%m'),
                  'provider_date': row['date'], 'provider_obs_date': obs_date,
                  'timestamp_pst': stamp.isoformat(), 'timestamp_utc': stamp.astimezone(UTC).isoformat(),
                  'observation_timestamp_pst': obs_stamp.isoformat() if obs_stamp else None,
                  'observation_timestamp_utc': obs_stamp.astimezone(UTC).isoformat() if obs_stamp else None,
                  'raw_value': raw, 'value': value, 'units': units,
                  'value_si': value * factor if value is not None else None, 'units_si': si_units,
                  'data_flag': row['dataFlag'], 'value_status': state}
        key = record['timestamp_pst']
        if key in records and records[key] != record:
            raise ValueError('Conflicting duplicate observation timestamps')
        records[key] = record
    return sorted(records.values(), key=lambda r: r['timestamp_pst'])


def summarize(records: list[dict], spec: dict, end: date) -> dict:
    labels = {r['period_label'] for r in records}
    expected = []
    day = date.fromisoformat(spec['start'])
    if spec['resolution'] == 'monthly':
        day = day.replace(day=1)
    while day <= end:
        expected.append(day.isoformat() if spec['resolution'] == 'daily' else day.strftime('%Y-%m'))
        day = (day + timedelta(days=1) if spec['resolution'] == 'daily' else
               date(day.year + (day.month == 12), day.month % 12 + 1, 1))
    numeric = [r for r in records if r['value'] is not None]
    return {'rows': len(records), 'numeric_rows': len(numeric),
            'missing_observation_timestamps': sum(not r['provider_obs_date'] for r in records),
            'missing_or_nonnumeric_rows': len(records) - len(numeric),
            'negative_numeric_rows': sum(r['value'] < 0 for r in numeric),
            'first_period': records[0]['period_label'] if records else None,
            'latest_period': records[-1]['period_label'] if records else None,
            'latest_numeric_period': numeric[-1]['period_label'] if numeric else None,
            'absent_periods': sorted(set(expected) - labels),
            'nonblank_flags': sorted({str(r['data_flag']) for r in records if str(r['data_flag']).strip()}),
            'coverage_scope': 'Advertised sensor start through requested date; gaps are reported, not filled'}


def acquire(root: Path, spec: dict, end: date) -> dict:
    started = datetime.now(UTC).isoformat()
    params = {'Stations': spec['station_id'], 'SensorNums': spec['sensor_number'],
              'dur_code': spec['duration_code'], 'Start': spec['start'], 'End': end.isoformat()}
    with requests.Session() as session:
        session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504))))
        response = session.get(ENDPOINT, params=params, timeout=(20, 180))
        response.raise_for_status()
        payload = response.content
        sha = hashlib.sha256(payload).hexdigest()
        raw = root / 'observations/inputs/cdec/fnf' / spec['resolution'] / spec['station_id'] / (sha + '.json.gz')
        atomic_bytes(raw, gzip.compress(payload, compresslevel=4, mtime=0))
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError(f'Empty/malformed full-history response: {response.url}')
        records = normalize(rows, spec, end)
        if not any(r['value'] is not None for r in records):
            raise ValueError('No numeric records in full-history response')
        # Reject unexpected history loss. Explicit missing records still replace old values.
        directory = root / 'observations/processed/cdec/fnf' / spec['resolution'] / spec['station_id']
        old_labels = set()
        for path in directory.glob('????.csv'):
            with path.open(newline='') as stream:
                old_labels.update(r['timestamp_pst'] for r in csv.DictReader(stream))
        new_labels = {r['timestamp_pst'] for r in records}
        if old_labels - new_labels:
            raise ValueError('Server omitted previously published records; retain local data and review raw response')
        changed, years = 0, sorted({r['timestamp_pst'][:4] for r in records})
        for year in years:
            buffer = io.StringIO(newline='')
            writer = csv.DictWriter(buffer, fieldnames=list(records[0]))
            writer.writeheader(); writer.writerows(r for r in records if r['timestamp_pst'].startswith(year))
            changed += atomic_bytes(directory / (year + '.csv'), buffer.getvalue().encode())
        report = spec | summarize(records, spec, end) | {
            'status': 'passed', 'request_url': response.url, 'retrieved_utc': started,
            'requested_end': end.isoformat(), 'source_sha256': sha, 'raw_file': str(raw.relative_to(root)),
            'year_files': len(years), 'year_files_changed': changed,
            'processed_directory': str(directory.relative_to(root)),
            'timezone': 'PST (UTC-08:00), fixed offset per CDEC web-service documentation',
            'interval_semantics': 'Provider period labels retained; UTC timestamp conversion does not redefine averaging intervals'}
        write_json(directory / 'manifest.json', report)
        return report
