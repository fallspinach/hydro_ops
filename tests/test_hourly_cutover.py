import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('cutover', Path(__file__).parents[1]/'bin/migrate_forcing_hourly_layout.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cutover_inventory_and_metadata(tmp_path):
    mod = module()
    relative = 'forcing/outputs/conus/retro/1981'
    source = tmp_path/relative
    source.mkdir(parents=True)
    data = source/'file.nc'
    data.write_bytes(b'unchanged')
    destination = 'forcing/outputs/conus/retro/hourly/1981'
    plan = {'conflicts': [], 'project_root': str(tmp_path),
            'proposed_moves': [{'source': relative, 'destination': destination}],
            'file_inventory': [{'source': relative+'/file.nc', 'destination': destination+'/file.nc',
                                'identity': mod.identity(data)}]}
    assert mod.validated_moves(plan, tmp_path) == [(source, tmp_path/destination)]
    value = {'published_identity': {'path': str(data), 'bytes': 9},
             'audit': str(tmp_path/'forcing/work/old-audit.json')}
    updated = mod.remap(value, tmp_path)
    assert updated['published_identity']['path'] == str(tmp_path/destination/'file.nc')
    assert updated['audit'] == value['audit']
    assert mod.remap(updated, tmp_path) == updated
    (source/'new.nc').write_bytes(b'extra')
    with pytest.raises(ValueError, match='complete'):
        mod.validated_moves(plan, tmp_path)
    plan['proposed_moves'][0]['destination'] = 'unrelated'
    with pytest.raises(ValueError, match='Destination'):
        mod.validated_moves(plan, tmp_path)
