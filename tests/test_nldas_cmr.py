from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
import requests

from hydro_ops.download.nldas2 import CMR_COLLECTION, CMR_URL, Nldas2Downloader


def entry(hour=0):
    name = f'NLDAS_FORA0125_H.A20260920.{hour:02}00.020.nc'
    return {'id': f'G{hour}', 'collection_concept_id': CMR_COLLECTION,
            'producer_granule_id': name, 'time_start': f'2026-09-20T{hour:02}:00:00Z',
            'links': [{'rel': 'http://esipfed.org/ns/fedsearch/1.1/data#',
                       'href': f'https://data.gesdisc.earthdata.nasa.gov/data/{name}'}]}


def downloader(tmp_path, monkeypatch, entries, *, hits=None, status=200):
    class Response:
        def __init__(self):
            self.headers = {'CMR-Hits': str(len(entries) if hits is None else hits)}
        def raise_for_status(self):
            if status != 200:
                raise requests.HTTPError(str(status))
        def json(self):
            return {'feed': {'entry': entries}}
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, params, timeout):
            assert url == CMR_URL
            assert params['collection_concept_id'] == CMR_COLLECTION
            assert params['temporal'] == '2026-09-20T00:00:00Z,2026-09-20T23:59:59Z'
            return Response()
    settings = SimpleNamespace(nldas_data_dir=tmp_path, nldas_connect_timeout=1, nldas_read_timeout=1)
    result = Nldas2Downloader(settings, check_credentials=False, discovery='cmr')
    monkeypatch.setattr(result, '_session', Session)
    return result


def test_cloud_urls_sorted_and_local_layout_preserved(tmp_path, monkeypatch):
    a = entry(12)
    a['links'] += [{'rel': 'http://esipfed.org/ns/fedsearch/1.1/data#',
                   'href': 'https://example.test/file.nc', 'inherited': True}]
    d = downloader(tmp_path, monkeypatch, [a, entry(0)])
    items = d.discover(date(2026, 9, 20))
    assert len(items) == 2
    assert items[0].destination == tmp_path / '2026/263/NLDAS_FORA0125_H.A20260920.0000.020.nc'
    assert items[1].url.startswith('https://data.gesdisc.earthdata.nasa.gov/')


@pytest.mark.parametrize('change', ['collection', 'date', 'time', 'host', 'inherited', 'duplicate', 'count'])
def test_cmr_rejects_wrong_or_ambiguous_results(tmp_path, monkeypatch, change):
    a = entry()
    if change == 'collection':
        a['collection_concept_id'] = 'other'
    if change == 'date':
        a['producer_granule_id'] = a['producer_granule_id'].replace('20260920', '20260919')
    if change == 'time':
        a['time_start'] = '2026-09-20T01:00:00Z'
    if change == 'host':
        a['links'][0]['href'] = a['links'][0]['href'].replace('data.gesdisc.earthdata.nasa.gov', 'example.test')
    if change == 'inherited':
        a['links'][0]['inherited'] = True
    items = [a, deepcopy(a)] if change == 'duplicate' else [a]
    d = downloader(tmp_path, monkeypatch, items, hits=2 if change == 'count' else len(items))
    with pytest.raises(ValueError):
        d.discover(date(2026, 9, 20))


def test_empty_success_is_unpublished_but_http_errors_are_not(tmp_path, monkeypatch):
    d = downloader(tmp_path, monkeypatch, [])
    assert d.discover(date(2026, 9, 20), allow_unpublished=True) == []
    with pytest.raises(RuntimeError):
        d.discover(date(2026, 9, 20))
    d = downloader(tmp_path, monkeypatch, [], status=404)
    with pytest.raises(requests.HTTPError):
        d.discover(date(2026, 9, 20), allow_unpublished=True)


def test_submit_forwards_cloud_and_latest_options(tmp_path, monkeypatch):
    from dataclasses import replace

    from hydro_ops import cli
    from hydro_ops.config import load_settings

    settings = replace(load_settings(), log_root=tmp_path)
    assert settings.nldas_discovery == 'cmr'
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.subprocess, 'run', run)
    args = cli.build_parser().parse_args(['submit', 'nldas2', '--discovery', 'cmr',
                                         '--discover-latest', '--date', '2026-09-20'])
    assert cli.submit_nldas2(args) == 0
    assert commands[0][commands[0].index('--discovery') + 1] == 'cmr'
    assert '--discover-latest' in commands[0]


def test_cloud_head_follows_redirects_before_freshness_check(tmp_path, monkeypatch):
    from email.utils import formatdate

    from hydro_ops.download.nldas2 import Granule

    path = tmp_path / 'sample.nc'
    path.write_bytes(b'CDF\x01sample')
    d = downloader(tmp_path, monkeypatch, [])
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def head(self, url, *, timeout, allow_redirects):
            assert allow_redirects is True
            return SimpleNamespace(raise_for_status=lambda: None,
                headers={'Content-Length': str(path.stat().st_size),
                         'Last-Modified': formatdate(path.stat().st_mtime, usegmt=True)})
    monkeypatch.setattr(d, '_session', Session)
    monkeypatch.setattr(d, '_save_cookies', lambda s: None)
    assert d.download_one(Granule('https://data.gesdisc.earthdata.nasa.gov/sample.nc', path)) == 'skipped'
