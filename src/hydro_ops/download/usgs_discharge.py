"""Lossless USGS v1 daily discharge acquisition and observed-period diagnostics."""
from __future__ import annotations

import csv
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from itertools import pairwise

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json
from hydro_ops.download.usgs_inventory import BASE, csv_bytes, sha256

FIELDS = ['observation_id', 'monitoring_location_id', 'time_series_id', 'provider_date',
          'raw_value', 'discharge_cfs', 'discharge_m3_s', 'unit_of_measure',
          'approval_status', 'qualifier', 'last_modified', 'value_status']


def paced_request(root, session, url, interval=5):
    """One shared request budget across all shards, including smoke/retries."""
    directory = root / 'observations/status/usgs/discharge'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'request-budget.lock').open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        previous = float(handle.read() or '0')
        delay = max(0, previous + interval - time.time())
        if delay:
            time.sleep(delay)
        handle.seek(0)
        handle.truncate()
        handle.write(str(time.time()))
        handle.flush()
    response = session.get(url, timeout=(20, 240))
    response.raise_for_status()
    return response


def daily_pages(root, raw, station, start, stop, *, limit=50000):
    """Persist and checksum every page; follow next links to exhaustion."""
    session = requests.Session()
    session.mount('https://', HTTPAdapter(max_retries=Retry(
        total=4, backoff_factor=3, status_forcelist=(429, 500, 502, 503, 504),
        respect_retry_after_header=True)))
    if key := os.environ.get('USGS_API_KEY'):
        session.headers['X-Api-Key'] = key
    url = requests.Request('GET', BASE + 'daily/items', params={
        'f': 'json', 'limit': limit, 'monitoring_location_id': station,
        'parameter_code': '00060', 'statistic_id': '00003',
        'datetime': start + '/' + stop}).prepare().url
    features, sources, seen_urls, seen_ids = [], [], set(), set()
    while url:
        if (not url.startswith(BASE + 'daily/items?') or 'api_key=' in url.lower()
                or url in seen_urls):
            raise ValueError('Unsafe/repeated daily pagination link')
        seen_urls.add(url)
        target = raw / f'page-{len(sources):04d}.json.gz'
        receipt = target.with_name(target.name + '.source.json')
        if target.exists() and not receipt.exists():
            # An interrupted raw/receipt pair is not an accepted cache. Preserve
            # the orphan for diagnosis, then safely re-fetch the unaccepted page.
            target.rename(target.with_name(target.name + '.orphan-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')))
        if target.exists():
            payload = gzip.decompress(target.read_bytes())
            provenance = json.loads(receipt.read_text())
            if provenance['url'] != url or provenance['sha256'] != hashlib.sha256(payload).hexdigest():
                raise ValueError('Cached daily response checksum/query mismatch')
        else:
            response = paced_request(root, session, url)
            payload = response.content
            decoded = json.loads(payload)
            if decoded.get('type') != 'FeatureCollection' or not isinstance(decoded.get('features'), list):
                raise ValueError('Invalid daily response; not cached')
            provenance = {'url': url, 'sha256': hashlib.sha256(payload).hexdigest(),
                          'retrieved_utc': datetime.now(UTC).isoformat(),
                          'raw_file': str(target.relative_to(root)),
                          'api_version': response.headers.get('api-version'),
                          'rate_limit': response.headers.get('X-RateLimit-Limit'),
                          'rate_remaining': response.headers.get('X-RateLimit-Remaining')}
            atomic_bytes(target, gzip.compress(payload, compresslevel=4, mtime=0))
            provenance['compressed_sha256'] = sha256(target)
            write_json(receipt, provenance)
        page = json.loads(payload)
        if page.get('type') != 'FeatureCollection' or not isinstance(page.get('features'), list):
            raise ValueError('Invalid daily response')
        if page.get('numberReturned', len(page['features'])) != len(page['features']):
            raise ValueError('Daily response row count mismatch')
        for feature in page['features']:
            if not feature['id'] or feature['id'] in seen_ids:
                raise ValueError('Missing/duplicate observation ID across pages')
            seen_ids.add(feature['id'])
        features.extend(page['features'])
        sources.append(provenance)
        next_links = [r['href'] for r in page.get('links', []) if r.get('rel') == 'next']
        if len(next_links) > 1 or (next_links and not page['features']):
            raise ValueError('Invalid daily pagination')
        url = next_links[0] if next_links else None
    return features, sources


def normalize(features, spec, start, stop):
    """Keep provider date-only labels: these are NOT UTC-midnight timestamps."""
    records, seen = [], set()
    for feature in features:
        row = feature['properties']
        if (row['monitoring_location_id'] != spec['monitoring_location_id']
                or row['parameter_code'] != '00060' or row['statistic_id'] != '00003'
                or row['time_series_id'] not in spec['series_metadata']
                or row['unit_of_measure'] != 'ft^3/s'):
            raise ValueError('Daily observation identity/series/units mismatch; refresh metadata if needed')
        day = date.fromisoformat(row['time'])
        if not date.fromisoformat(start) <= day <= date.fromisoformat(stop):
            raise ValueError('Daily observation outside requested date interval')
        key = (row['time_series_id'], row['time'])
        if key in seen:
            raise ValueError('Multiple observations for one series/day')
        seen.add(key)
        raw = row.get('value')
        try:
            value = float(raw)
        except (ValueError, TypeError):
            value = None
        qualifier = row.get('qualifier')
        flags = qualifier if isinstance(qualifier, list) else [qualifier]
        invalid_flag = any(str(f).upper() in ('INVALID', 'MISSING', 'REJECTED') for f in flags)
        if value is None or not math.isfinite(value):
            value, status = None, 'missing_or_nonnumeric'
        elif invalid_flag:
            value, status = None, 'explicit_invalid_qualifier'
        else:
            status = 'numeric_negative' if value < 0 else 'numeric'
        records.append({'observation_id': feature['id'],
            'monitoring_location_id': row['monitoring_location_id'],
            'time_series_id': row['time_series_id'], 'provider_date': row['time'],
            'raw_value': raw, 'discharge_cfs': value,
            'discharge_m3_s': value * .028316846592 if value is not None else None,
            'unit_of_measure': row['unit_of_measure'], 'approval_status': row.get('approval_status'),
            'qualifier': qualifier, 'last_modified': row.get('last_modified'), 'value_status': status})
    return sorted(records, key=lambda r: (r['provider_date'], r['time_series_id']))


def span_metrics(valid_days, first, last):
    """Internal missing days, including leap days; no artificial missing pre-establishment years."""
    days = sorted(d.toordinal() for d in valid_days if first <= d <= last)
    expected = (last - first).days + 1
    padded = [first.toordinal() - 1, *days, last.toordinal() + 1]
    return {'expected_days': expected, 'valid_days': len(days),
            'completeness': len(days) / expected,
            'longest_missing_gap_days': max(b - a - 1 for a, b in pairwise(padded))}


def diagnostics(records, spec, start, stop):
    """Distinct-date coverage, not averaging different sublocations or primary series."""
    primary = {key for key, row in spec['series_metadata'].items() if row.get('primary') == 'Primary'}
    eligible = primary or set(spec['series_metadata'])
    days = defaultdict(list)
    for row in records:
        if row['time_series_id'] in eligible:
            days[date.fromisoformat(row['provider_date'])].append(row)
    valid, approved, estimated = set(), set(), set()
    conflicts = 0
    for day, candidates in days.items():
        numeric = [r for r in candidates if r['discharge_cfs'] is not None]
        values = {r['discharge_cfs'] for r in numeric}
        if len(values) > 1:
            conflicts += 1
            continue
        if numeric:
            valid.add(day)
            if any(r['approval_status'] == 'Approved' for r in numeric):
                approved.add(day)
            if any('ESTIM' in str(r['qualifier']).upper()
                   or str(r['qualifier']).lower() == 'e' for r in numeric):
                estimated.add(day)
    report = {'monitoring_location_id': spec['monitoring_location_id'],
        'nwm_feature_id': spec['nwm_feature_id'], 'rows': len(records),
        'series_count_returned': len({r['time_series_id'] for r in records}),
        'diagnostic_series_policy': 'primary_only' if primary else 'nonprimary_no_primary_metadata',
        'conflicting_primary_date_count': conflicts, 'approved_valid_days': len(approved),
        'estimated_valid_days': len(estimated),
        'negative_numeric_rows': sum(r['value_status'] == 'numeric_negative' for r in records),
        'requested_start': start, 'requested_end': stop,
        'first_valid_date': min(valid).isoformat() if valid else None,
        'last_valid_date': max(valid).isoformat() if valid else None,
        'qualifiers': dict(Counter(str(r['qualifier']) for r in records)),
        'value_status_counts': dict(Counter(r['value_status'] for r in records))}
    first, last = (min(valid), max(valid)) if valid else (None, None)
    report['observed_span'] = span_metrics(valid, first, last) if valid else None
    report['full_requested_period'] = span_metrics(valid, date.fromisoformat(start), date.fromisoformat(stop))
    annual = []
    if valid:
        for year in range(first.year, last.year + 1):
            begin, end = max(first, date(year, 1, 1)), min(last, date(year, 12, 31))
            metrics = span_metrics(valid, begin, end)
            annual.append({'monitoring_location_id': spec['monitoring_location_id'],
                'nwm_feature_id': spec['nwm_feature_id'], 'year': year,
                'period_start': begin.isoformat(), 'period_end': end.isoformat(), **metrics})
    report['years_with_data'] = len({d.year for d in valid})
    report['full_calendar_years_ge90pct'] = sum(
        r['period_start'].endswith('01-01') and r['period_end'].endswith('12-31')
        and r['completeness'] >= .9 for r in annual)
    return report, annual


def accepted(root, receipt, spec, plan):
    if not receipt.exists():
        return None
    record = json.loads(receipt.read_text())
    if (record.get('status') != 'passed' or record.get('plan_sha256') != plan['plan_sha256']
            or record.get('monitoring_location_id') != spec['monitoring_location_id']):
        raise ValueError('Existing station publication belongs to a different plan')
    for path, expected in record['outputs_sha256'].items():
        if sha256(root / path) != expected:
            raise ValueError('Accepted station output checksum changed')
    for source in record['sources']:
        if sha256(root / source['raw_file']) != source['compressed_sha256']:
            raise ValueError('Accepted source response is missing or changed')
    return record


def acquire(root, state, spec, plan):
    station = spec['monitoring_location_id']
    target = root / 'observations/processed/usgs/discharge/daily' / station
    target.mkdir(parents=True, exist_ok=True)
    with (target / 'write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = target / 'acceptance.json'
        prior = accepted(root, receipt, spec, plan)
        if prior:
            return {'status': 'passed', 'station': station, 'skipped': True,
                    'receipt': str(receipt.relative_to(root)), 'rows': prior['rows']}
        marker = target / 'plan.json'
        if marker.exists() and json.loads(marker.read_text())['plan_sha256'] != plan['plan_sha256']:
            raise ValueError('Unrecognized existing station staging files')
        if not marker.exists() and any(p.name not in ('write.lock',) for p in target.iterdir()):
            raise ValueError('Refusing unrecognized station outputs')
        write_json(marker, {'plan_sha256': plan['plan_sha256']})
        raw = root / 'observations/inputs/usgs/discharge/daily' / state.name / station
        features, sources = daily_pages(root, raw, station, plan['start'], plan['end'])
        records = normalize(features, spec, plan['start'], plan['end'])
        summary, annual = diagnostics(records, spec, plan['start'], plan['end'])
        payload = csv_bytes(records, FIELDS)
        history = target / 'history.csv.gz'
        atomic_bytes(history, gzip.compress(payload, compresslevel=4, mtime=0))
        if gzip.decompress(history.read_bytes()) != payload:
            raise ValueError('Processed history transfer verification failed')
        if len(list(csv.DictReader(io.StringIO(payload.decode())))) != len(features):
            raise ValueError('Source-to-processed row coverage mismatch')
        write_json(target / 'station.json', spec)
        write_json(target / 'diagnostics.json', summary)
        atomic_bytes(target / 'annual_completeness.csv', csv_bytes(annual, [
            'monitoring_location_id', 'nwm_feature_id', 'year', 'period_start', 'period_end',
            'expected_days', 'valid_days', 'completeness', 'longest_missing_gap_days']))
        outputs = {str(p.relative_to(root)): sha256(p) for p in (
            history, target / 'station.json', target / 'diagnostics.json',
            target / 'annual_completeness.csv')}
        record = {'status': 'passed', 'monitoring_location_id': station,
                  'plan_sha256': plan['plan_sha256'], 'rows': len(records),
                  'source_to_published_rows_verified': True, 'sources': sources,
                  'outputs_sha256': outputs, 'completed_utc': datetime.now(UTC).isoformat()}
        write_json(receipt, record)
        return {'status': 'passed', 'station': station, 'skipped': False,
                'receipt': str(receipt.relative_to(root)), 'rows': len(records)}


def safe_acquire(root, state, spec, plan):
    # A second bounded attempt can recover a transient response/write failure;
    # raw page caches and accepted station receipts avoid redoing completed work.
    for attempt in range(2):
        try:
            return acquire(root, state, spec, plan)
        except (ValueError, OSError, KeyError, requests.RequestException) as error:
            if attempt:
                return {'status': 'failed', 'station': spec['monitoring_location_id'], 'error': str(error)}
            time.sleep(2)
    raise AssertionError('Unreachable')
