import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from hydro_ops.download.usgs_inventory import (
    BASE,
    CONUS_STATES,
    Client,
    assemble,
    csv_bytes,
    filter_record_period,
    select_domain,
    valid_point,
)


def sample():
    station = {'monitoring_location_id': 'USGS-00123456', 'agency_code': 'USGS',
               'monitoring_location_number': '00123456', 'country_code': 'US',
               'state_code': '06', 'time_zone_abbreviation': 'PST',
               'uses_daylight_savings': 'Y', 'site_type_code': 'ST'}
    series = {'id': 'series-1', 'monitoring_location_id': station['monitoring_location_id'],
              'parameter_code': '00060', 'statistic_id': '00003',
              'computation_period_identifier': 'Daily', 'begin': '1900-01-01T08:00:00Z',
              'end': '2026-10-26T07:00:00Z', 'primary': None, 'unit_of_measure': 'ft^3/s'}
    geom = {'type': 'Point', 'coordinates': [-120, 38]}
    combined = {'id': 'series-1', 'properties': station | series | {'data_type': 'Daily values'},
                'geometry': geom}
    daily = {'id': 'series-1', 'properties': series, 'geometry': geom}
    zones = [{'id': 'PST', 'properties': {'time_zone_utc_offset': '-08:00',
                                        'time_zone_daylight_savings_utc_offset': '-07:00'}}]
    return combined, daily, list(station), zones


def test_inventory_preserves_ids_timezone_native_endpoints_and_nonprimary():
    combined, daily, fields, zones = sample()
    stations, series, excluded = assemble([combined], [daily], fields, zones)
    assert excluded == {}
    assert stations[0]['monitoring_location_number'] == '00123456'
    assert stations[0]['uses_daylight_savings'] == 'Y'
    assert stations[0]['time_zone_definition']['time_zone_utc_offset'] == '-08:00'
    assert series[0]['provider_time_series'] == daily['properties']
    assert series[0]['provider_combined'] == combined['properties']
    assert '00123456' in csv_bytes(stations).decode()
    assert len(CONUS_STATES) == 49


def test_inventory_crosscheck_filters_and_metadata_conflicts():
    combined, daily, fields, zones = sample()
    with pytest.raises(ValueError, match='mismatch'):
        assemble([combined], [], fields, zones)
    wrong = deepcopy(daily)
    wrong['properties']['statistic_id'] = '00001'
    with pytest.raises(ValueError, match='ignored'):
        assemble([combined], [wrong], fields, zones)
    duplicate, second = deepcopy(combined), deepcopy(daily)
    duplicate['id'] = second['id'] = 'series-2'
    duplicate['properties']['time_zone_abbreviation'] = 'MST'
    with pytest.raises(ValueError, match='Conflicting'):
        assemble([combined, duplicate], [daily, second], fields, zones)


def test_inventory_multiple_series_missing_metadata_and_exclusions():
    combined, daily, fields, zones = sample()
    duplicate, second = deepcopy(combined), deepcopy(daily)
    duplicate['id'] = second['id'] = 'series-2'
    stations, series, _ = assemble([combined, duplicate], [daily, second], fields, zones)
    assert len(stations) == 1 and len(series) == 2
    combined['geometry'] = None
    stations, _, _ = assemble([combined], [daily], fields, [])
    assert stations[0]['longitude'] is None and stations[0]['coordinate_issue']
    assert stations[0]['time_zone_issue']
    duplicate['properties']['state_code'] = '02'
    stations, _, excluded = assemble([combined, duplicate], [daily, second], fields, zones)
    assert len(stations) == 1 and excluded['outside_conus_series'] == 1


def test_reporting_boundary_covers_edges_but_no_buffer():
    shapely = pytest.importorskip('shapely.geometry')
    boundary = shapely.box(-121, 37, -119, 39)
    stations = [{'longitude': -120, 'latitude': 38}, {'longitude': -121, 'latitude': 38},
                {'longitude': -121.001, 'latitude': 38}, {'longitude': None, 'latitude': None}]
    assert select_domain(stations, boundary) == stations[:2]
    assert not valid_point({'type': 'Point', 'coordinates': [float('nan'), 38]})


def test_pagination_follows_all_pages_and_rejects_duplicate_ids(tmp_path, monkeypatch):
    client = Client(tmp_path)
    records = [
        {'type': 'FeatureCollection', 'features': [{'id': 'a'}], 'numberReturned': 1,
         'links': [{'rel': 'next', 'href': BASE + 'time-series-metadata/items?cursor=next'}]},
        {'type': 'FeatureCollection', 'features': [{'id': 'b'}], 'numberReturned': 1}]
    iterator = iter(records)
    monkeypatch.setattr(client, 'fetch', lambda *_: next(iterator))
    assert [f['id'] for f in client.pages('time-series-metadata', {})] == ['a', 'b']
    records[1]['features'][0]['id'] = 'a'
    iterator = iter(records)
    with pytest.raises(ValueError, match='duplicate'):
        client.pages('time-series-metadata', {})
    with pytest.raises(ValueError, match='origin'):
        Client(tmp_path).fetch('https://other.example/items', 'bad')


def test_cached_pages_verified_and_keys_not_in_urls(tmp_path, monkeypatch):
    monkeypatch.setenv('USGS_API_KEY', 'test-private-key')
    client = Client(tmp_path)
    assert client.session.headers['X-Api-Key'] == 'test-private-key'
    with pytest.raises(ValueError, match='credentials'):
        client.fetch(BASE + 'time-zone-codes/items?api_key=secret', 'bad')
    import gzip
    import hashlib
    data = json.dumps({'type': 'FeatureCollection', 'features': []}).encode()
    path = tmp_path / 'page.json.gz'
    path.write_bytes(gzip.compress(data))
    receipt = {'url': BASE + 'time-zone-codes/items',
               'sha256': hashlib.sha256(data).hexdigest()}
    path.with_name(path.name + '.source.json').write_text(json.dumps(receipt))
    assert client.fetch(receipt['url'], 'page')['features'] == []
    receipt['sha256'] = 'wrong'
    path.with_name(path.name + '.source.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='checksum'):
        client.fetch(receipt['url'], 'page')


def test_record_filter_includes_1979_and_preserves_timezone_metadata():
    combined, daily, fields, zones = sample()
    stations, series, _ = assemble([combined], [daily], fields, zones)
    cutoff = datetime(1979, 1, 1, tzinfo=UTC)
    original = deepcopy(stations)
    series[0]['provider_time_series']['end'] = '1979-01-01T00:00:00+00:00'
    kept, selected, report = filter_record_period(stations, series, cutoff)
    assert kept == original and selected == series
    assert report['stations_with_confirmed_overlap'] == 1
    series[0]['provider_time_series']['end'] = '1978-12-31T23:59:59Z'
    assert filter_record_period(stations, series, cutoff)[:2] == ([], [])


def test_record_filter_multiple_series_unknown_and_invalid_endpoints():
    combined, daily, fields, zones = sample()
    stations, series, _ = assemble([combined], [daily], fields, zones)
    cutoff = datetime(1979, 1, 1, tzinfo=UTC)
    older = deepcopy(series[0])
    older['id'] = 'old'
    older['provider_time_series']['end'] = '1978-01-01T08:00:00Z'
    kept, selected, report = filter_record_period(stations, [older, *series], cutoff)
    assert kept == stations and selected == series and report['stations_excluded'] == 0
    series[0]['provider_time_series']['end'] = None
    kept, selected, report = filter_record_period(stations, series, cutoff)
    assert kept == stations and selected == series
    assert report['stations_with_only_unknown_overlap'] == 1
    series[0]['provider_time_series']['end'] = '1979-01-01T00:00:00'
    with pytest.raises(ValueError, match='timezone-aware'):
        filter_record_period(stations, series, cutoff)
    with pytest.raises(ValueError, match='timezone-aware'):
        filter_record_period(stations, series, cutoff.replace(tzinfo=None))


def test_filter_exports_confirmed_and_unknown_without_changing_source(tmp_path, monkeypatch):
    import importlib
    from pathlib import Path

    from hydro_ops.download.cdec_fnf import write_json
    from hydro_ops.download.usgs_inventory import DOMAINS, sha256

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('filter_usgs_streamflow_inventory')
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'bin/filter_inventory.py'))
    combined, daily, fields, zones = sample()
    stations, series, _ = assemble([combined], [daily], fields, zones)
    for suffix, end in [('old', '1978-01-01T08:00:00Z'), ('unknown', None)]:
        station, record = deepcopy(stations[0]), deepcopy(series[0])
        station['monitoring_location_id'] += suffix
        record['monitoring_location_id'] = station['monitoring_location_id']
        record['provider_time_series']['monitoring_location_id'] = station['monitoring_location_id']
        record['provider_time_series']['end'] = end
        stations.append(station)
        series.append(record)
    source = tmp_path / 'snapshot'
    write_json(source / 'stations.json', {'stations': stations})
    write_json(source / 'time_series.json', {'time_series': series})
    for domain in DOMAINS:
        target = source / 'domains' / domain / 'station_ids.txt'
        target.parent.mkdir(parents=True)
        target.write_text('\n'.join(s['monitoring_location_id'] for s in stations))
    parent = {'status': 'passed', 'files_sha256': {
        str(p.relative_to(source)): sha256(p) for p in source.rglob('*') if p.is_file()},
        'domain_counts': {d: {'stations': 3} for d in DOMAINS}}
    write_json(source / 'acceptance.json', parent)
    before = sha256(source / 'acceptance.json')
    monkeypatch.setattr('sys.argv', ['filter', '--snapshot', str(source), '--since', '1979-01-01'])
    module.main()
    output = tmp_path / 'observations/static/stations/usgs/streamflow_selections/snapshot-since-19790101-confirmed'
    report = json.loads((output / 'acceptance.json').read_text())
    assert report['status'] == 'passed' and report['stations'] == 1
    assert report['stations_excluded'] == 1
    assert report['stations_with_only_unknown_overlap'] == 1
    assert report['domain_counts']['yampa']['stations'] == 1
    assert report['domain_counts']['yampa']['unknown_record_end_stations'] == 1
    assert len(json.loads((output / 'unknown_record_end/stations.json').read_text())['stations']) == 1
    assert sha256(source / 'acceptance.json') == before
    with pytest.raises(FileExistsError):
        module.main()
