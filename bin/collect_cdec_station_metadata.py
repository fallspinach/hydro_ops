"""Snapshot official CDEC station metadata without downloading observations."""
import csv
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = 'https://cdec.water.ca.gov'


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean(value):
    parser = Text()
    parser.feed(value)
    parser.close()
    return ' '.join(' '.join(parser.parts).split())


def parse_metadata(html, station_id, *, allow_missing_coordinates=False):
    tables = re.findall(r'<table\b[^>]*>.*?</table>', html, re.IGNORECASE | re.DOTALL)
    info = next((t for t in tables if re.search(r'id=[\"\x27]tableInfo[\"\x27]', t)), None)
    if info is None:
        raise ValueError('Station metadata table missing')
    cells = re.findall(r'<td\b[^>]*>(.*?)</td>', info, re.IGNORECASE | re.DOTALL)
    if len(cells) % 2:
        raise ValueError('Odd number of metadata fields')
    fields = dict(zip(map(clean, cells[::2]), map(clean, cells[1::2])))
    if fields.get('Station ID') != station_id:
        raise ValueError('Returned station ID mismatch')
    headings = re.findall(r'<h2\b[^>]*>(.*?)</h2>', html[:html.index(info)], re.IGNORECASE | re.DOTALL)
    if not headings:
        raise ValueError('Station name missing')
    coordinates, coordinate_issues = {}, []
    for name, bound in [('Latitude', 90), ('Longitude', 180)]:
        try:
            value = float(fields[name].replace('°', ''))
            if not -bound <= value <= bound:
                raise ValueError('Out of range')
        except (KeyError, ValueError):
            if not allow_missing_coordinates:
                raise ValueError(f'Invalid/missing station {name}') from None
            value = None
            coordinate_issues.append(name)
        coordinates[name.lower()] = value
    record = {'station_id': station_id, 'station_name': clean(headings[-1]), 'provider_fields': fields,
              **coordinates,
              'elevation_raw': fields.get('Elevation'), 'river_basin': fields.get('River Basin'),
              'hydrologic_area': fields.get('Hydrologic Area'), 'county': fields.get('County'),
              'nearby_city': fields.get('Nearby City'), 'operator': fields.get('Operator'),
              'maintenance': fields.get('Maintenance')}
    if allow_missing_coordinates:
        record['coordinate_issues'] = coordinate_issues
    sensor_tables = [t for t in tables if 'Sensor Description' in t and 'Sensor Number' in t]
    if len(sensor_tables) != 1:
        raise ValueError('Expected one sensor table')
    sensors = []
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', sensor_tables[0], re.IGNORECASE | re.DOTALL):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if not cells:
            continue
        if len(cells) != 6:
            raise ValueError('Unexpected sensor row schema')
        description_units, number, duration, _, collection, available = map(clean, cells)
        description, units = description_units.rsplit(',', 1)
        links = re.findall(r'href=[\"\x27](.*?)[\"\x27]', row, re.IGNORECASE)
        plot = next((urljoin(BASE, link) for link in links if 'sensor_no=' in link), None)
        duration = duration.strip('() ').lower()
        sensor = {'station_id': station_id, 'sensor_number': int(number),
                  'description': description.strip(), 'units': units.strip(),
                  'duration': duration, 'duration_code': {'daily': 'D', 'monthly': 'M',
                    'hourly': 'H', 'event': 'E'}.get(duration),
                  'data_collection': collection, 'advertised_availability': available,
                  'plot_sensor_id': parse_qs(urlsplit(plot).query).get('sensor_no', [None])[0] if plot else None,
                  'plot_url': plot, 'is_full_natural_flow': 'FULL NATURAL' in description.upper()}
        sensors.append(sensor)
    notes = []
    for table in tables:
        if table == info or table in sensor_tables:
            continue
        for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table, re.IGNORECASE | re.DOTALL):
            cells = [clean(c) for c in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)]
            if len(cells) == 2 and re.fullmatch(r'\d{2}/\d{2}/\d{4}', cells[0]):
                notes.append({'date_raw': cells[0], 'text': cells[1]})
    record.update(sensors=sensors, historical_notes=notes)
    return record


def write_csv(path, records):
    with path.open('x', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)


def main():
    root = Path(__file__).resolve().parents[1]
    ids = json.loads((root / 'config/cdec_fnf_stations.json').read_text())['station_ids']
    if len(ids) != 24 or len(set(ids)) != 24 or any(not re.fullmatch('[A-Z]{3}', s) for s in ids):
        raise ValueError('Expected 24 unique station IDs')
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    output = root / 'observations/static/stations/cdec' / stamp
    output.mkdir(parents=True, exist_ok=False)
    raw = root / 'observations/inputs/cdec/station_metadata' / stamp
    raw.mkdir(parents=True, exist_ok=False)
    session = requests.Session()
    session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504))))
    records, failures = [], []
    for station in ids:
        url = BASE + '/dynamicapp/staMeta?station_id=' + station
        try:
            response = session.get(url, timeout=(15, 60)); response.raise_for_status()
            content = response.content
            (raw / f'{station}.html').write_bytes(content)
            record = parse_metadata(content.decode('utf-8'), station)
            record.update(source_url=url, retrieved_utc=datetime.now(UTC).isoformat(),
                          source_sha256=hashlib.sha256(content).hexdigest(),
                          raw_file=str((raw / f'{station}.html').relative_to(root)))
            records.append(record)
            print(station, record['station_name'], [(s['sensor_number'], s['duration'], s['advertised_availability'])
                  for s in record['sensors'] if s['is_full_natural_flow']], flush=True)
        except (requests.RequestException, ValueError, KeyError) as error:
            failures.append({'station_id': station, 'error': str(error)})
        time.sleep(.2)
    report = {'status': 'passed' if len(records) == len(ids) else 'failed',
              'requested_station_ids': ids, 'station_count': len(records), 'failures': failures,
              'availability_semantics': 'Provider-advertised sensor range, not verified time-series completeness',
              'stations': records}
    (output / 'stations.json').write_text(json.dumps(report, indent=2) + '\n')
    if records:
        flat = [{k:v for k,v in r.items() if k not in ('sensors', 'historical_notes', 'provider_fields')} for r in records]
        write_csv(output / 'stations.csv', flat)
        sensors = [s for r in records for s in r['sensors']]
        write_csv(output / 'sensors.csv', sensors)
        write_csv(output / 'fnf_sensors.csv', [s for s in sensors if s['is_full_natural_flow']])
        geo = {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
               'geometry': {'type': 'Point', 'coordinates': [r['longitude'], r['latitude']]},
               'properties': {k:v for k,v in r.items() if k not in ('sensors', 'historical_notes', 'provider_fields')}} for r in records]}
        (output / 'stations.geojson').write_text(json.dumps(geo, indent=2) + '\n')
    print(output)
    if failures:
        raise RuntimeError(str(failures))


if __name__ == '__main__':
    main()
