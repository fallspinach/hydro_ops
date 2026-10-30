"""Snapshot USGS v1 daily-discharge metadata without changing provider time semantics."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import time
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json

BASE = 'https://api.waterdata.usgs.gov/ogcapi/v1/collections/'
# The contiguous 48 US states plus DC. Not a rectangular geographic shortcut.
CONUS_STATES = frozenset(['01', '04', '05', '06', '08', '09', '10', '11', '12', '13', '16', '17', '18', '19', '20', '21', '22', '23', '24', '25', '26', '27', '28', '29', '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40', '41', '42', '44', '45', '46', '47', '48', '49', '50', '51', '53', '54', '55', '56'])
DOMAINS = {
    'cnrfc': ('cnrfc', 'cnrfc', 'cnrfc_hu18_union'),
    'b120': ('b120', 'b120', 'b120_union'),
    'ucrb': ('ucrb', 'ucrb', 'ucrb_reporting'),
    'yampa': ('yampa', 'yampa', 'yampa_reporting'),
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Client:
    """Serial, retrying, provenance-preserving client; never record API keys."""

    def __init__(self, raw):
        self.raw = raw
        self.session = requests.Session()
        self.session.mount('https://', HTTPAdapter(max_retries=Retry(
            total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
            respect_retry_after_header=True)))
        if key := os.environ.get('USGS_API_KEY'):
            self.session.headers['X-Api-Key'] = key
        self.sources = []

    def fetch(self, url, name):
        if not url.startswith(BASE) or urlparse(url).hostname != 'api.waterdata.usgs.gov':
            raise ValueError('Unexpected metadata pagination origin')
        if 'api_key=' in url.lower():
            raise ValueError('API credentials must be passed in headers, not URLs')
        target = self.raw / (name + '.json.gz')
        receipt = target.with_name(target.name + '.source.json')
        if target.exists():
            content = gzip.decompress(target.read_bytes())
            source = json.loads(receipt.read_text())
            if (source['url'] != url
                    or source['sha256'] != hashlib.sha256(content).hexdigest()):
                raise ValueError('Cached metadata checksum/query mismatch')
        else:
            response = self.session.get(url, timeout=(20, 180))
            response.raise_for_status()
            content = response.content
            # Do not cache an error page, even if the server returned HTTP 200.
            json.loads(content)
            source = {'url': url, 'sha256': hashlib.sha256(content).hexdigest(),
                      'retrieved_utc': datetime.now(UTC).isoformat(),
                      'raw_file': str(target), 'etag': response.headers.get('ETag'),
                      'last_modified': response.headers.get('Last-Modified')}
            atomic_bytes(target, gzip.compress(content, compresslevel=4, mtime=0))
            write_json(receipt, source)
            time.sleep(.7)
        self.sources.append(source)
        return json.loads(content)

    def pages(self, collection, params):
        url = requests.Request('GET', BASE + collection + '/items',
                               params={'f': 'json', 'limit': 1000, **params}).prepare().url
        records, seen, ids = [], set(), set()
        page = 0
        while url:
            if url in seen:
                raise ValueError('Repeated pagination link')
            seen.add(url)
            data = self.fetch(url, f'{collection}-{page:04d}')
            page += 1
            if data.get('type') != 'FeatureCollection' or not isinstance(data.get('features'), list):
                raise ValueError('Expected a GeoJSON feature collection')
            features = data['features']
            if data.get('numberReturned', len(features)) != len(features):
                raise ValueError('Metadata page count mismatch')
            for feature in features:
                identity = feature['id']
                if not identity or identity in ids:
                    raise ValueError('Missing/duplicate metadata feature ID')
                ids.add(identity)
            records.extend(features)
            links = [link['href'] for link in data.get('links', []) if link.get('rel') == 'next']
            if len(links) > 1 or (links and not features):
                raise ValueError('Invalid metadata pagination')
            url = links[0] if links else None
            print(json.dumps({'collection': collection, 'pages': page, 'records': len(records)}),
                  flush=True)
        return records


def valid_point(geometry):
    if not geometry or geometry.get('type') != 'Point':
        return False
    xy = geometry.get('coordinates', [])
    return (isinstance(xy, (list, tuple)) and len(xy) >= 2
            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in xy[:2])
            and -180 <= xy[0] <= 180 and -90 <= xy[1] <= 90)


def assemble(combined, daily, station_fields, zones):
    """Cross-check national series IDs, then retain CONUS stations/series separately."""
    left = {f['id']: f for f in combined}
    right = {f['id']: f for f in daily}
    if len(left) != len(combined) or len(right) != len(daily) or set(left) != set(right):
        raise ValueError(f'Combined/daily series inventory mismatch: '
                         f'{len(set(left) - set(right))} combined-only, '
                         f'{len(set(right) - set(left))} daily-only')
    if not left:
        raise ValueError('Unexpected empty national daily-discharge inventory')
    stations, series, excluded = {}, [], Counter()
    zone_map = {f['id']: f['properties'] for f in zones}
    for identity, feature in sorted(left.items()):
        props = feature['properties']
        native = right[identity]['properties']
        for row in (props, native):
            if row.get('parameter_code') != '00060' or row.get('statistic_id') != '00003':
                raise ValueError('API ignored discharge/mean filter')
        if (props.get('data_type') != 'Daily values'
                or native.get('computation_period_identifier') != 'Daily'
                or props['monitoring_location_id'] != native['monitoring_location_id']):
            raise ValueError('Daily-series identity/type mismatch')
        if props.get('agency_code') != 'USGS' or not props['monitoring_location_id'].startswith('USGS-'):
            excluded['non_usgs_series'] += 1
            continue
        if props.get('country_code') != 'US' or props.get('state_code') not in CONUS_STATES:
            excluded['outside_conus_series'] += 1
            continue
        station_id = props['monitoring_location_id']
        station = {k: props.get(k) for k in station_fields if k not in ('id', 'geometry')}
        station['monitoring_location_id'] = station_id
        geometry = feature.get('geometry')
        station['longitude'] = geometry['coordinates'][0] if valid_point(geometry) else None
        station['latitude'] = geometry['coordinates'][1] if valid_point(geometry) else None
        station['coordinate_issue'] = None if valid_point(geometry) else 'Missing/invalid point'
        timezone = zone_map.get(station.get('time_zone_abbreviation'), {})
        station['time_zone_definition'] = timezone
        station['time_zone_issue'] = (None if timezone else 'Missing/unrecognized USGS time-zone code')
        if station_id in stations and stations[station_id] != station:
            raise ValueError(f'Conflicting station metadata across series: {station_id}')
        stations[station_id] = station
        # Preserve the unmodified provider records as well as the normalized keys.
        series.append({'id': identity, 'monitoring_location_id': station_id,
                       'provider_combined': props, 'provider_time_series': native})
    if not stations:
        raise ValueError('No CONUS stations selected')
    return [stations[k] for k in sorted(stations)], series, dict(excluded)


def csv_bytes(rows, fields=None):
    fields = fields or sorted({key for row in rows for key in row})
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                         for k, v in row.items()})
    return stream.getvalue().encode()


def station_features(stations):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'id': s['monitoring_location_id'], 'properties': s,
         'geometry': None if s['longitude'] is None else
         {'type': 'Point', 'coordinates': [s['longitude'], s['latitude']]}} for s in stations]}


def select_domain(stations, geometry):
    from shapely.geometry import Point
    # covers includes stations exactly on the reporting boundary. No arbitrary buffer.
    return [s for s in stations if s['longitude'] is not None
            and geometry.covers(Point(s['longitude'], s['latitude']))]


def filter_record_period(stations, series, since):
    """Keep stations with any series ending on/after an aware cutoff; retain unknowns."""
    if since.tzinfo is None:
        raise ValueError('Record cutoff must be timezone-aware')
    selected, unknown = [], set()
    station_ids = {s['monitoring_location_id'] for s in stations}
    qualifying = set()
    for row in series:
        station = row['monitoring_location_id']
        if station not in station_ids:
            raise ValueError('Time series references an unknown station')
        native = row['provider_time_series']
        end = native.get('end')
        if end is None or end == '':
            unknown.add(station)
            selected.append(row)
            continue
        timestamp = datetime.fromisoformat(end)
        if timestamp.tzinfo is None:
            raise ValueError('Provider record endpoint must be timezone-aware')
        if timestamp >= since:
            qualifying.add(station)
            selected.append(row)
    kept_ids = qualifying | unknown
    kept = [s for s in stations if s['monitoring_location_id'] in kept_ids]
    return kept, selected, {'stations_with_confirmed_overlap': len(qualifying),
        'stations_with_only_unknown_overlap': len(unknown - qualifying),
        'stations_excluded': len(station_ids - kept_ids),
        'unknown_end_series': sum(not s['provider_time_series'].get('end') for s in selected)}
