"""Discover all CDEC sensor-3/82 stations and snapshot their metadata."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import requests
from collect_cdec_station_metadata import BASE, parse_metadata, write_csv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json

LOCAL = threading.local()


def session():
    if not hasattr(LOCAL, 'session'):
        LOCAL.session = requests.Session()
        LOCAL.session.mount('https://', HTTPAdapter(max_retries=Retry(total=3,
            backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))
    return LOCAL.session


def station_ids(html, *, allow_empty=False):
    ids = sorted(set(re.findall(r'staMeta\?station_id=([A-Z0-9]{3})(?=[\"\x27&\s<>])', html)))
    if not ids and not (allow_empty and re.search(r'id=[\"\x27]station_table[\"\x27]', html)):
        raise ValueError('No station links found in search results')
    if re.search(r'>\s*(?:next page|next|[»])\s*</a>', html, re.IGNORECASE):
        raise ValueError('Unexpected search pagination; inspect before using incomplete list')
    return ids


def fetch(url, params, target):
    provenance_file = target.with_name(target.name + '.source.json')
    if target.exists():
        content = gzip.decompress(target.read_bytes())
        request = requests.Request('GET', url, params=params).prepare()
        resolved = request.url
        if provenance_file.exists():
            provenance = json.loads(provenance_file.read_text())
            if provenance['source_sha256'] != hashlib.sha256(content).hexdigest():
                raise ValueError('Cached source checksum mismatch')
            return content, provenance
    else:
        response = session().get(url, params=params, timeout=(20, 90))
        response.raise_for_status()
        content, resolved = response.content, response.url
        atomic_bytes(target, gzip.compress(content, compresslevel=4, mtime=0))
        time.sleep(.2)
    provenance = {'source_url': resolved, 'source_sha256': hashlib.sha256(content).hexdigest(),
                  'raw_file': str(target),
                  'retrieved_utc': datetime.fromtimestamp(target.stat().st_mtime, UTC).isoformat()}
    write_json(provenance_file, provenance)
    return content, provenance


def discover(raw):
    result = {}
    for number in (3, 82):
        queries = []
        for name, extra in [('any', {}), ('active', {'active_chk': 'on', 'active': 'Y'}),
                            ('discontinued', {'active_chk': 'on', 'active': 'N'}),
                            *[(d, {'dur_chk': 'on', 'dur': d}) for d in ('H', 'D', 'M', 'E')]]:
            params = {'sensor_chk': 'on', 'sensor': number, 'display': 'sta', **extra}
            content, provenance = fetch(BASE + '/dynamicapp/staSearch', params,
                                       raw / f'search-{number}-{name}.html.gz')
            ids = station_ids(content.decode('utf-8'), allow_empty=name != 'any')
            queries.append(provenance | {'filter': name, 'station_ids': ids, 'count': len(ids)})
        # Use the union of independent duration/status searches to guard defaults.
        ids = sorted({s for query in queries for s in query['station_ids']})
        result[str(number)] = {'station_ids': ids, 'count': len(ids), 'queries': queries}
    return result


def collect(station, raw):
    content, provenance = fetch(BASE + '/dynamicapp/staMeta', {'station_id': station},
                               raw / 'stations' / (station + '.html.gz'))
    record = parse_metadata(content.decode('utf-8'), station, allow_missing_coordinates=True)
    return record | provenance


def export(output, report):
    records = sorted(report['stations'], key=lambda r: r['station_id'])
    report['stations'] = records
    write_json(output / 'stations.json', report)
    if not records:
        return
    # Output directory is isolated; atomic replacement permits safe resume.
    def csv(name, rows):
        if not rows:
            return
        temporary = output / ('.' + name + '.tmp')
        temporary.unlink(missing_ok=True)
        write_csv(temporary, rows)
        temporary.replace(output / name)

    flat = [{k: v for k, v in r.items() if k not in ('sensors', 'historical_notes', 'provider_fields')}
            for r in records]
    csv('stations.csv', flat)
    for number in (3, 82):
        station_rows, sensors = [], []
        for row, record in zip(flat, records):
            matches = [s for s in record['sensors'] if s['sensor_number'] == number]
            if matches:
                station_rows.append(row | {'sensor_number': number,
                    'durations': ';'.join(sorted({s['duration'] for s in matches}))})
                sensors.extend(s | {'station_name': record['station_name']} for s in matches)
        csv(f'sensor_{number}_stations.csv', station_rows)
        csv(f'sensor_{number}_series.csv', sensors)
    geo = {'type': 'FeatureCollection', 'features': []}
    for r in flat:
        coordinates = [r['longitude'], r['latitude']]
        geo['features'].append({'type': 'Feature', 'properties': r,
            'geometry': None if None in coordinates else {'type': 'Point', 'coordinates': coordinates}})
    write_json(output / 'stations.geojson', geo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', type=Path, help='Existing SWE metadata snapshot directory')
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Use slurm/collect_cdec_swe_metadata.sh for the station-wide crawl')
    root = Path(__file__).resolve().parents[1]
    stamp = (args.resume.name if args.resume else datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    output = root / 'observations/static/stations/cdec/swe' / stamp
    if args.resume and args.resume.resolve() != output:
        raise ValueError('Resume must identify a canonical SWE snapshot')
    output.mkdir(parents=True, exist_ok=bool(args.resume))
    lock = (output / '.collection.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    raw = root / 'observations/inputs/cdec/swe_station_metadata' / stamp
    raw.mkdir(parents=True, exist_ok=True)
    state = root / 'observations/status/cdec/swe-metadata' / ('job-' + os.environ['SLURM_JOB_ID'])
    state.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'],
              'output': str(output.relative_to(root)), 'raw_directory': str(raw.relative_to(root)),
              'requested_sensor_numbers': [3, 82], 'stations': [], 'failures': [],
              'sensor_semantics': {'3': 'SNOW, WATER CONTENT', '82': 'SNOW, WATER CONTENT(REVISED)'},
              'scope': 'All stations in CDEC search, any status and duration; no geographic filter',
              'time_series_downloaded': False}
    try:
        discovery = discover(raw)
        write_json(output / 'discovery.json', discovery)
        ids = sorted({s for group in discovery.values() for s in group['station_ids']})
        report.update(expected_stations=len(ids), discovery_counts={k: v['count'] for k,v in discovery.items()})
        write_json(state / 'status.json', {k:v for k,v in report.items() if k != 'stations'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(collect, station, raw): station for station in ids}
            for future in as_completed(futures):
                station = futures[future]
                try:
                    record = future.result()
                    actual = {s['sensor_number'] for s in record['sensors']}
                    expected = {int(n) for n,g in discovery.items() if station in g['station_ids']}
                    if not expected.issubset(actual):
                        raise ValueError(f'Search/metadata sensor mismatch: expected {expected}, found {actual}')
                    record['requested_sensors_present'] = sorted(actual & {3, 82})
                    report['stations'].append(record)
                except (requests.RequestException, ValueError, KeyError, OSError) as error:
                    report['failures'].append({'station_id': station, 'error': str(error)})
                report['completed_stations'] = len(report['stations'])
                write_json(state / 'status.json', {k:v for k,v in report.items() if k != 'stations'})
                if (len(report['stations']) + len(report['failures'])) % 25 == 0:
                    export(output, report)
        report['status'] = 'failed' if report['failures'] else 'passed'
    except (requests.RequestException, ValueError, KeyError, OSError) as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        export(output, report)
        write_json(state / 'status.json', {k:v for k,v in report.items() if k != 'stations'})
    print(json.dumps({k:v for k,v in report.items() if k != 'stations'}), flush=True)
    if report['failures']:
        raise RuntimeError('Station metadata failures; inspect report and resume the same snapshot')


if __name__ == '__main__':
    main()
