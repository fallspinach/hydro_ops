import gzip
import json
from datetime import date

import pytest

from hydro_ops.download.cdec_fnf import (
    acquire,
    atomic_bytes,
    normalize,
    series_from_metadata,
    summarize,
)


def spec(resolution='daily'):
    return {'station_id': 'AMF', 'resolution': resolution, 'start': '2024-01-01',
            'sensor_number': 8 if resolution == 'daily' else 65,
            'duration_code': 'D' if resolution == 'daily' else 'M'}


def row(value=100, day='2024-1-1 00:00', duration='D'):
    return {'stationId': 'AMF', 'durCode': duration, 'SENSOR_NUM': 8 if duration == 'D' else 65,
            'date': day, 'obsDate': day, 'value': value, 'dataFlag': 'e',
            'units': 'CFS' if duration == 'D' else 'AF'}


def test_units_flags_and_fixed_pst():
    records = normalize([row(), row(-9999, '2024-7-1 00:00'), row(-2, '2024-7-2 00:00')],
                        spec(), date(2024, 7, 2))
    assert records[0]['value_si'] == pytest.approx(2.8316846592)
    assert records[0]['data_flag'] == 'e'
    assert records[1]['timestamp_utc'] == '2024-07-01T08:00:00+00:00'
    assert records[1]['raw_value'] == -9999 and records[1]['value_si'] is None
    assert records[2]['value'] == -2 and records[2]['value_status'] == 'negative'
    missing_date = row(); missing_date.pop('obsDate')
    parsed = normalize([missing_date], spec(), date(2024, 1, 2))
    assert parsed[0]['value'] == 100
    assert parsed[0]['observation_timestamp_utc'] is None
    assert summarize(parsed, spec(), date(2024, 1, 2))['missing_observation_timestamps'] == 1
    monthly = normalize([row(1, duration='M')], spec('monthly'), date(2024, 1, 31))
    assert monthly[0]['value_si'] == pytest.approx(1233.48183754752)
    assert monthly[0]['units_si'] == 'm3'
    assert monthly[0]['period_label'] == '2024-01'


def test_identity_and_duplicate_guards():
    with pytest.raises(ValueError, match='mismatch'):
        normalize([row() | {'stationId': 'TNL'}], spec(), date(2024, 1, 2))
    with pytest.raises(ValueError, match='duplicate'):
        normalize([row(), row(101)], spec(), date(2024, 1, 2))
    with pytest.raises(ValueError, match='outside'):
        normalize([row(day='2023-1-1 00:00')], spec(), date(2024, 1, 2))


def test_monthly_coverage():
    records = normalize([row(1, duration='M'), row(-9999, '2024-3-1 00:00', 'M')],
                        spec('monthly'), date(2024, 3, 31))
    report = summarize(records, spec('monthly'), date(2024, 3, 31))
    assert report['absent_periods'] == ['2024-02']
    assert report['latest_numeric_period'] == '2024-01'
    assert report['missing_or_nonnumeric_rows'] == 1


def test_metadata_selects_only_fnf_not_accumulation():
    sensor = lambda n, dur, u: {'sensor_number': n, 'duration_code': dur, 'units': u,
                              'advertised_availability': '10/01/1900 to present'}
    metadata = {'status': 'passed', 'stations': [
        {'station_id': 'AMF', 'source_url': 'amf', 'sensors': [sensor(8, 'D', 'CFS'),
            sensor(65, 'M', 'AF'), sensor(290, 'D', 'AF')]},
        {'station_id': 'TNL', 'source_url': 'tnl', 'sensors': [sensor(65, 'M', 'AF')]}]}
    selected = series_from_metadata(metadata, ['AMF', 'TNL'])
    assert len(selected) == 3 and all(r['start'] == '1900-10-01' for r in selected)
    assert {r['sensor_number'] for r in selected} == {8, 65}


def test_revision_and_history_loss_guard(tmp_path, monkeypatch):
    payload = [row()]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def mount(self, *args): pass
        def get(self, url, params, timeout):
            assert params['Start'] == '2024-01-01'
            class Response:
                content = json.dumps(payload).encode()
                def raise_for_status(self): pass
                def json(self): return payload
            result = Response(); result.url = url
            return result

    monkeypatch.setattr('hydro_ops.download.cdec_fnf.requests.Session', Session)
    a = acquire(tmp_path, spec(), date(2024, 1, 2))
    assert json.loads(gzip.decompress((tmp_path / a['raw_file']).read_bytes())) == payload
    assert a['year_files_changed'] == 1
    assert acquire(tmp_path, spec(), date(2024, 1, 2))['year_files_changed'] == 0
    payload[0] = row(200)
    assert acquire(tmp_path, spec(), date(2024, 1, 2))['year_files_changed'] == 1
    file = tmp_path / a['processed_directory'] / '2024.csv'
    before = file.read_bytes()
    payload[0] = row(300, '2024-1-2 00:00')
    with pytest.raises(ValueError, match='omitted'):
        acquire(tmp_path, spec(), date(2024, 1, 2))
    assert file.read_bytes() == before
    assert atomic_bytes(file, before) is False
