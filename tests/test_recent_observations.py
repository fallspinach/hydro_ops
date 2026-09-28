from datetime import date
from pathlib import Path
from types import SimpleNamespace

import requests

from hydro_ops.forcing import recent_observations as module


def test_mrms_available_and_missing(monkeypatch):
    monkeypatch.setattr(module, 'load_settings', lambda: SimpleNamespace(mrms_products=('pass1', 'pass2', 'quality')))
    calls = []
    class Downloader:
        def __init__(self, settings):
            pass
        def file(self, product, valid):
            return SimpleNamespace(netcdf=Path(product), product=product, valid=valid)
        def download_one(self, item):
            calls.append(item)
            if item.product == 'pass2':
                response = requests.Response()
                response.status_code = 404
                raise requests.HTTPError(response=response)
            return 'downloaded'
    monkeypatch.setattr(module, 'MrmsDownloader', Downloader)
    monkeypatch.setattr(module, 'is_netcdf', lambda p: p.name == 'quality')
    result = module.acquire('mrms', date(2026, 9, 28), 1)
    assert result == {'source': 'mrms', 'downloaded': 2, 'skipped': 2,
                      'unavailable': 2, 'errors': [], 'status': 'passed'}
    assert {i.valid.hour for i in calls} == {0, 1}


def test_stage4_filters_and_converts(monkeypatch):
    monkeypatch.setattr(module, 'load_settings', lambda: None)
    downloaded = []
    converted = []
    class Downloader:
        def __init__(self, settings):
            pass
        def discover_realtime(self, day):
            return [SimpleNamespace(destination=Path(f'st4_conus.20260928{h}.{step}h.grb2'))
                    for h, step in [('00', '01'), ('01', '01'), ('02', '01'), ('06', '06'), ('00', '24')]]
        def download_one(self, item):
            downloaded.append(item.destination)
    class Converter:
        def __init__(self, settings):
            pass
        def destination(self, name, stream):
            return Path(name)
        def convert_grib2(self, path, stream):
            converted.append(path)
    monkeypatch.setattr(module, 'Stage4Downloader', Downloader)
    monkeypatch.setattr(module, 'Stage4Converter', Converter)
    monkeypatch.setattr(module, 'is_netcdf', lambda p: '2800.01h' in p.name)
    result = module.acquire('stage4', date(2026, 9, 28), 1)
    assert result['downloaded'] == 2
    assert result['skipped'] == 1
    assert downloaded == converted
    assert all('24h' not in p.name and '2802' not in p.name for p in downloaded)


def test_refresh_timeout_and_failure(monkeypatch, tmp_path):
    killed = []
    class Process:
        pid = 123
        def __init__(self, command, **kwargs):
            self.source = command[command.index('--source') + 1]
        def wait(self, timeout=None):
            if self.source == 'mrms' and timeout is not None:
                raise module.subprocess.TimeoutExpired('test', timeout)
            return 1
    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    monkeypatch.setattr(module.os, 'killpg', lambda pid, sig: killed.append(pid))
    result = module.refresh_recent_observations(tmp_path, date(2026, 9, 28), 11, timeout=1)
    assert [r['status'] for r in result] == ['timeout', 'degraded']
    assert killed == [123]
