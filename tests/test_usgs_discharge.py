import gzip
import json
from datetime import date

import pytest

from hydro_ops.download import usgs_discharge as module


def spec():
    return {'monitoring_location_id': 'USGS-00123456', 'nwm_feature_id': '123',
            'time_zone_abbreviation': 'PST', 'uses_daylight_savings': 'Y',
            'series_metadata': {'series': {'primary': 'Primary'}}}


def feature(day='2020-02-28', value='1', series='series', qualifier=None):
    return {'type': 'Feature', 'id': day + series, 'properties': {
        'monitoring_location_id': 'USGS-00123456', 'time_series_id': series,
        'parameter_code': '00060', 'statistic_id': '00003', 'time': day,
        'value': value, 'unit_of_measure': 'ft^3/s', 'approval_status': 'Approved',
        'qualifier': qualifier, 'last_modified': '2026-10-28T00:00:00Z'}}


def test_normalization_native_date_units_zero_negative_missing_flags():
    rows = [feature('2020-02-28', '0'), feature('2020-02-29', '-2'),
            feature('2020-03-01', None), feature('2020-03-02', 'Ice'),
            feature('2020-03-03', '1', qualifier='e'),
            feature('2020-03-04', '1', qualifier='INVALID')]
    data = module.normalize(rows, spec(), '2020-02-28', '2020-03-04')
    assert data[0]['provider_date'] == '2020-02-28'
    assert data[0]['discharge_cfs'] == 0
    assert data[1]['discharge_m3_s'] == pytest.approx(-2 * .028316846592)
    assert data[2]['discharge_cfs'] is None and data[3]['raw_value'] == 'Ice'
    assert data[4]['qualifier'] == 'e' and data[4]['discharge_cfs'] == 1
    assert data[5]['value_status'] == 'explicit_invalid_qualifier'
    for bad in ([rows[0], rows[0]], [feature('1978-01-01')], [feature(series='unknown')]):
        with pytest.raises(ValueError):
            module.normalize(bad, spec(), '2020-02-28', '2020-03-04')


def test_diagnostics_leap_day_recent_station_and_internal_gaps():
    rows = module.normalize([feature('2020-02-28'), feature('2020-03-01')],
                            spec(), '1979-01-01', '2020-03-01')
    report, years = module.diagnostics(rows, spec(), '1979-01-01', '2020-03-01')
    assert report['observed_span']['expected_days'] == 3
    assert report['observed_span']['completeness'] == pytest.approx(2 / 3)
    assert report['observed_span']['longest_missing_gap_days'] == 1
    assert report['full_requested_period']['completeness'] < .001
    assert len(years) == 1 and years[0]['year'] == 2020
    assert report['full_calendar_years_ge90pct'] == 0
    assert module.span_metrics(set(), date(2020, 1, 1), date(2020, 1, 5))['longest_missing_gap_days'] == 5


def test_multiple_series_do_not_inflate_or_average_conflicting_days():
    station = spec()
    station['series_metadata'].update(other={'primary': 'Primary'}, temporary={'primary': None})
    rows = module.normalize([feature(), feature(series='other'),
                             feature('2020-02-29', '2'), feature('2020-02-29', '3', 'other'),
                             feature('2020-03-01', '5', 'temporary')],
                            station, '2020-02-28', '2020-03-01')
    report, _ = module.diagnostics(rows, station, '2020-02-28', '2020-03-01')
    assert report['observed_span']['valid_days'] == 1
    assert report['conflicting_primary_date_count'] == 1
    assert report['last_valid_date'] == '2020-02-28'


def test_daily_pagination_raw_cache_and_duplicate_detection(tmp_path, monkeypatch):
    next_url = module.BASE + 'daily/items?cursor=two'
    pages = [
        {'type': 'FeatureCollection', 'features': [feature()], 'numberReturned': 1,
         'links': [{'rel': 'next', 'href': next_url}]},
        {'type': 'FeatureCollection', 'features': [feature('2020-02-29')], 'numberReturned': 1}]
    calls = []

    def request(root, session, url):
        from types import SimpleNamespace
        calls.append(url)
        return SimpleNamespace(content=json.dumps(pages[len(calls) - 1]).encode(), headers={})

    monkeypatch.setattr(module, 'paced_request', request)
    raw = tmp_path / 'raw'
    rows, sources = module.daily_pages(tmp_path, raw, 'USGS-00123456', '2020-02-28', '2020-02-29')
    assert len(rows) == len(sources) == len(calls) == 2
    assert 'limit=50000' in calls[0]
    assert module.daily_pages(tmp_path, raw, 'USGS-00123456', '2020-02-28', '2020-02-29')[0] == rows
    assert len(calls) == 2
    source = sources[0]
    assert module.sha256(tmp_path / source['raw_file']) == source['compressed_sha256']
    pages[1]['features'] = pages[0]['features']
    calls.clear()
    with pytest.raises(ValueError, match='duplicate'):
        module.daily_pages(tmp_path, tmp_path / 'duplicates', 'USGS-00123456', '2020-02-28', '2020-02-29')


def test_publication_resume_and_corruption_guard(tmp_path, monkeypatch):
    station = spec()
    plan = {'plan_sha256': 'pinned', 'start': '2020-02-28', 'end': '2020-03-01'}
    calls = []

    def fetch(*args):
        calls.append(True)
        return [feature(), feature('2020-03-01', None)], []

    monkeypatch.setattr(module, 'daily_pages', fetch)
    state = tmp_path / 'campaign'
    result = module.acquire(tmp_path, state, station, plan)
    assert result['status'] == 'passed' and result['rows'] == 2
    assert module.acquire(tmp_path, state, station, plan)['skipped']
    assert len(calls) == 1
    target = tmp_path / 'observations/processed/usgs/discharge/daily/USGS-00123456'
    assert b'2020-02-28' in gzip.decompress((target / 'history.csv.gz').read_bytes())
    (target / 'history.csv.gz').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        module.acquire(tmp_path, state, station, plan)


def test_no_valid_values_produces_explicit_empty_diagnostics():
    records = module.normalize([feature(value=None)], spec(), '2020-02-28', '2020-03-01')
    report, annual = module.diagnostics(records, spec(), '2020-02-28', '2020-03-01')
    assert report['observed_span'] is None and not annual
    assert report['full_requested_period']['valid_days'] == 0
