import importlib
from pathlib import Path

import pytest


def test_discovery_ids_and_pagination(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    parser = importlib.import_module('collect_cdec_swe_metadata')
    html = "<a href='/dynamicapp/staMeta?station_id=3LK'>3LK</a><a href='/dynamicapp/staMeta?station_id=BKL'>BKL</a>"
    assert parser.station_ids(html) == ['3LK', 'BKL']
    with pytest.raises(ValueError, match='No station'):
        parser.station_ids('<html>error</html>')
    assert parser.station_ids('<table id="station_table"></table>', allow_empty=True) == []
    with pytest.raises(ValueError, match='No station'):
        parser.station_ids('<html>error</html>', allow_empty=True)
    with pytest.raises(ValueError, match='pagination'):
        parser.station_ids(html + '<a href="page=2">Next</a>')


def test_missing_coordinates_preserved(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    parser = importlib.import_module('collect_cdec_station_metadata').parse_metadata
    html = '''<h2>Snow Course</h2><table id='tableInfo'>
    <tr><td>Station ID</td><td>3LK</td><td>Latitude</td><td>NA</td></tr>
    <tr><td>Longitude</td><td>-120</td></tr></table>
    <table><th>Sensor Description</th><th>Sensor Number</th>
    <tr><td>SNOW, WATER CONTENT, INCHES</td><td>3</td><td>(monthly)</td>
    <td></td><td>MANUAL ENTRY</td><td>01/01/1950 to present</td></tr></table>'''
    with pytest.raises(ValueError, match='Latitude'):
        parser(html, '3LK')
    record = parser(html, '3LK', allow_missing_coordinates=True)
    assert record['latitude'] is None and record['coordinate_issues'] == ['Latitude']
    assert record['sensors'][0]['description'] == 'SNOW, WATER CONTENT'
