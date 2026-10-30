import json
from datetime import datetime, timedelta

import pytest

from hydro_ops.wrf_hydro.nrt_recovery import file_identity, reuse_prefix, snapshot_forcing
from hydro_ops.wrf_hydro.production import restart_names, write_json


def test_snapshot_survives_atomic_update(tmp_path):
    start = datetime(2026, 10, 1)  # noqa: DTZ001
    source, destination = tmp_path/'source', tmp_path/'snapshot'
    for day in (start, start+timedelta(days=1)):
        path = source / day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'old data')
        write_json(path.with_name(path.name+'.subset.json'), {
            'status': 'passed', 'missing_active_values': 0, 'output_identity': file_identity(path)})
    records = snapshot_forcing(source, destination, start, start+timedelta(days=1))
    original = next(iter(records))
    path = source/'2026/10/20261001.LDASIN_DOMAIN1'
    replacement = tmp_path/'replacement'
    replacement.write_bytes(b'new data')
    replacement.replace(path)
    frozen = destination/'2026/10/20261001.LDASIN_DOMAIN1'
    assert frozen.read_bytes() == b'old data'
    assert file_identity(frozen) == records[original]
    assert file_identity(path) != records[original]
    with pytest.raises(ValueError, match='receipt'):
        snapshot_forcing(source, tmp_path/'invalid', start, start)


def test_reuse_prefix_and_reject_changed_input(tmp_path):
    start = datetime(2026, 3, 2)  # noqa: DTZ001
    stop, end = start+timedelta(days=1), start+timedelta(days=2)
    source, target = tmp_path/'old', tmp_path/'new'
    forcing = tmp_path/'forcing'
    forcing.write_text('unchanged')
    write_json(source/'cycle.json', {'status': 'failed', 'test': False, 'rst_typ': 1,
               'historical_nrt_refresh': True, 'start': str(start), 'end': str(end)})
    marker = source/'runs/2026/20260302-20260303/accepted.json'
    write_json(marker, {'start': str(start), 'end': str(stop), 'passed': True, 'rst_typ': 1,
               'forcing_identities': {str(forcing): file_identity(forcing)}})
    files = [source/'outputs/daily/2026/03'/f'20260302.{v}_DOMAIN1.daily'
             for v in ('LDASOUT', 'CHRTOUT')]
    for day in (start, stop):
        path = source/'outputs/hourly'/day.strftime('%Y/%m/%Y%m%d.CHRTOUT_DOMAIN1')
        files.extend([path, path.with_name(path.name+'.archive.json')])
    files.extend(source/'restarts/2026/03'/name for name in restart_names(stop))
    for path in files:
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text('preserved')
    cursor, report = reuse_prefix(source, target, start, end)
    assert cursor == stop and report['days'] == 1 and report['files'] == 8
    assert all(file_identity(p) == file_identity(target/p.relative_to(source)) for p in files)
    assert json.loads(marker.read_text()).get('reused_from') is None
    assert json.loads((target/marker.relative_to(source)).read_text())['reused_from'] == str(marker)
    forcing.write_text('changed')
    with pytest.raises(ValueError, match='forcing changed'):
        reuse_prefix(source, tmp_path/'bad', start, end)
