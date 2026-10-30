import gzip
import json
from datetime import date

import pytest

from hydro_ops.download.cdec_swe import (
    acquire,
    canonical,
    csv_bytes,
    directory,
    normalize,
    series,
    tasks_for,
)


def spec(code='H'):
    return {'station_id': 'BKL', 'sensor_number': 3, 'duration_code': code,
            'resolution': {'H': 'hourly', 'D': 'daily', 'M': 'monthly', 'E': 'event'}[code],
            'start': '2024-01-01', 'advertised_end': None}


def task(code='H', start='2024-12-31', end='2024-12-31'):
    return spec(code) | {'request_start': start, 'request_end': end, 'request_id': start + end + code}


def row(stamp='2024-12-31 23:00', value=1):
    return {'stationId': 'BKL', 'SENSOR_NUM': 3, 'durCode': 'H', 'units': 'INCHES',
            'date': stamp, 'value': value, 'dataFlag': 'e'}


def test_swe_end_boundary_units_and_missing():
    normalized = normalize([row(), row('2025-01-01 00:00'), row('2024-12-30 23:00')], task())
    assert len(normalized) == 1 and normalized[0]['swe_mm'] == 25.4
    assert normalized[0]['timestamp_utc'] == '2025-01-01T07:00:00+00:00'
    assert normalized[0]['provider_obs_date'] is None and normalized[0]['data_flag'] == 'e'
    assert normalize([row(value=-9999)], task())[0]['swe_mm'] is None
    assert normalize([row(value=-1)], task())[0]['value_status'] == 'negative_swe'
    with pytest.raises(ValueError, match='duplicate'):
        normalize([row(), row(value=2)], task())
    with pytest.raises(ValueError, match='identity'):
        normalize([row() | {'SENSOR_NUM': 82}], task())
    assert canonical(normalized)[0]['swe_mm'] == '25.4'


def test_bounded_plans_and_catchup(tmp_path):
    specifications = [spec('H') | {'start': '2023-12-31'}, spec('M')]
    tasks = tasks_for(tmp_path, specifications, date(2025, 1, 2), 'backfill')
    assert len(tasks) == 4
    hours = sorted((t for t in tasks if t['duration_code'] == 'H'), key=lambda t: t['request_start'])
    assert hours[0]['request_end'] == '2023-12-31'
    assert hours[1]['request_start'] == '2024-01-01' and hours[1]['request_end'] == '2024-12-31'
    assert hours[2]['request_start'] == '2025-01-01'
    cursor = directory(tmp_path, spec('H')) / 'cursor.json'
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({'checked_through': '2024-01-02'}))
    update = tasks_for(tmp_path, [spec('H')], date(2024, 2, 1), 'update')
    assert min(t['request_start'] for t in update) == '2024-01-03'


def test_series_keeps_two_sensors_and_native_durations():
    sensor = {'sensor_number': 3, 'duration_code': 'M', 'units': 'INCHES',
              'advertised_availability': '02/01/1930 to 04/01/2004'}
    metadata = {'status': 'passed', 'stations': [{'station_id': '3LK', 'sensors': [
        sensor, sensor | {'sensor_number': 82}]}]}
    selected = series(metadata)
    assert len(selected) == 2 and selected[0]['advertised_end'] == '2004-04-01'
    assert selected[0]['start'] == '1930-02-01'
    metadata['stations'][0]['sensors'][0]['advertised_availability'] = 'to 12/31/1987'
    assert series(metadata)[0]['start'] == '1800-01-01'
    assert series(metadata)[0]['start_source'] == 'explicit_request_floor_missing_provider_start'


def test_acquisition_merge_resume_empty_and_history_loss(tmp_path, monkeypatch):
    payload = [row()]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def mount(self, *args): pass
        def get(self, url, params, timeout):
            assert params['Start'] == '2024-12-30' and params['End'] == '2025-01-02'
            class Response:
                content = json.dumps(payload).encode()
                def raise_for_status(self): pass
                def json(self): return payload
            response = Response(); response.url = url
            return response

    monkeypatch.setattr('hydro_ops.download.cdec_swe.requests.Session', Session)
    monkeypatch.setattr('hydro_ops.download.cdec_swe.time.sleep', lambda _: None)
    args = (tmp_path, task(), False)
    result = acquire(args)
    assert result['status'] == 'passed'
    target = directory(tmp_path, task()) / '2024.csv.gz'
    assert gzip.decompress(target.read_bytes()) == csv_bytes(canonical(normalize(payload, task())))
    assert acquire(args)['status'] == 'skipped'
    payload[0] = row(value=2)
    acquire((tmp_path, task(), True))
    before = target.read_bytes()
    payload.clear()
    with pytest.raises(ValueError, match='omitted'):
        acquire((tmp_path, task(), True))
    assert target.read_bytes() == before
    empty = task() | {'station_id': 'EMP'}
    assert acquire((tmp_path, empty, True))['rows'] == 0


def test_monthly_single_history_file(tmp_path, monkeypatch):
    payload = [row('2024-01-01 00:00', 2) | {'durCode': 'M'},
               row('2025-01-01 00:00', 3) | {'durCode': 'M'}]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def mount(self, *args): pass
        def get(self, url, params, timeout):
            class Response:
                content = json.dumps(payload).encode()
                def raise_for_status(self): pass
                def json(self): return payload
            response = Response(); response.url = url
            return response

    monkeypatch.setattr('hydro_ops.download.cdec_swe.requests.Session', Session)
    monkeypatch.setattr('hydro_ops.download.cdec_swe.time.sleep', lambda _: None)
    request = task('M', '2024-01-01', '2025-12-31')
    result = acquire((tmp_path, request, True))
    assert len(result['receipt']) > 0
    assert (directory(tmp_path, request) / 'history.csv.gz').is_file()
    assert not list(directory(tmp_path, request).glob('????.csv.gz'))
    payload[:] = [row('2025-01-01 00:00', 4) | {'durCode': 'M'}]
    partial = task('M', '2025-01-01', '2025-12-31')
    acquire((tmp_path, partial, True))
    import csv
    import io
    records = list(csv.DictReader(io.StringIO(gzip.decompress(
        (directory(tmp_path, request) / 'history.csv.gz').read_bytes()).decode(), newline='')))
    assert len(records) == 2
    assert records[0]['value_inches'] == '2.0' and records[1]['value_inches'] == '4.0'
