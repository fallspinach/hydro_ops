import importlib
from pathlib import Path

import pytest


def test_station_parser(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    parser = importlib.import_module('collect_cdec_station_metadata').parse_metadata
    html = '''<h2>TEST &amp; RIVER</h2><table id='tableInfo'>
    <tr><td>Station ID</td><td>AMF</td><td>Latitude</td><td>38.6&#176</td></tr>
    <tr><td>Longitude</td><td>-121.2&#176</td><td>Elevation</td><td>0 ft</td></tr></table>
    <table><th>Sensor Description</th><th>Sensor Number</th>
    <tr><td>FULL NATURAL FLOW, CFS</td><td>8</td><td>(daily)</td>
    <td><a href="/plot?sensor_no=123">FNF</a></td><td>COMPUTED</td><td>01/01/1980 to present</td></tr></table>
    <table><tr><td>10/02/2025</td><td>Method changed.</td></tr></table>'''
    r = parser(html, 'AMF')
    assert r['station_name'] == 'TEST & RIVER'
    assert r['longitude'] == -121.2
    assert r['elevation_raw'] == '0 ft'
    assert r['sensors'][0]['duration_code'] == 'D'
    assert r['sensors'][0]['plot_sensor_id'] == '123'
    assert r['historical_notes'][0]['text'] == 'Method changed.'
    with pytest.raises(ValueError, match='ID mismatch'):
        parser(html, 'YRS')
    with pytest.raises(ValueError, match='missing'):
        parser('<html>Error</html>', 'AMF')
