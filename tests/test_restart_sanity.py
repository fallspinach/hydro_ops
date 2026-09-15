import importlib.util
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def test_active_land_screening(tmp_path):
    spec = importlib.util.spec_from_file_location('restart_audit', Path(__file__).parents[1]/'bin/audit_spinup_restarts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path/'restart.nc'
    with Dataset(path, 'w') as data:
        for name, size in [('Time', 1), ('south_north', 2), ('soil_layers_stag', 2), ('west_east', 2)]:
            data.createDimension(name, size)
        var = data.createVariable('SOIL_T', 'f4', ('Time', 'south_north', 'soil_layers_stag', 'west_east'), fill_value=-1e33)
        var[:] = 280
        var[0, 0, :, 1] = -1e33
        var[0, 1, 0, 0] = 400
    with Dataset(path) as data:
        data.set_auto_maskandscale(False)
        result = module.inventory(data['SOIL_T'], np.array([[True, False], [True, True]]))
    assert result['count'] == 6
    assert result['missing'] == 0
    assert result['out_of_bounds'] == 1
