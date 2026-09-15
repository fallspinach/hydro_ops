"""Read-only, tiled sanity inventory of the five CONUS spin-up restart pairs.

Broad bounds are screening checks, not a hydrologic-equilibration assessment.
Routing-grid inventories include inactive cells and must be interpreted as such.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, chartostring

BOUNDS = {'SOIL_T': (150, 350), 'TG': (150, 350), 'SMC': (-1e-6, 1.000001),
          'SH2O': (-1e-6, 1.000001), 'SNEQV': (-1e-6, None), 'SNOWH': (-1e-6, None),
          'CANLIQ': (-1e-6, None), 'CANICE': (-1e-6, None), 'ISNOW': (-3, 0),
          'FWET': (-1e-6, 1.000001)}


def inventory(variable, land):
    dims = variable.dimensions
    axis = next((i for i, d in enumerate(dims) if d in ('south_north', 'iy', 'iyrt')), 0)
    step = 64 if dims[axis] in ('south_north', 'iy', 'iyrt') else 65536
    stats = {'count': 0, 'missing': 0, 'min': None, 'max': None, 'sum': 0.0, 'negative': 0, 'out_of_bounds': 0}
    active = 'south_north' in dims or ('iy' in dims and variable.shape[axis] == land.shape[0])
    stats['scope'] = 'active_1km_land' if active else 'all_routing_cells_or_links_including_inactive'
    for begin in range(0, variable.shape[axis], step):
        selection = [slice(None)]*variable.ndim
        selection[axis] = slice(begin, begin+step)
        time_axes = [i for i, d in enumerate(dims) if d == 'Time']
        for i in time_axes:
            selection[i] = 0
        values = np.asarray(variable[tuple(selection)])
        if active:
            mask = land[begin:begin+step]
            if values.ndim == 3:
                mask = np.broadcast_to(mask[:, None, :], values.shape)
            values = values[mask]
        values = values.reshape(-1)
        valid = np.isfinite(values) & (np.abs(values) < 1e30)
        for name in ('_FillValue', 'missing_value'):
            if name in variable.ncattrs():
                valid &= ~np.isin(values, variable.getncattr(name))
        stats['count'] += values.size
        stats['missing'] += int((~valid).sum())
        values = values[valid].astype('f8')
        if not values.size:
            continue
        stats['min'] = float(values.min()) if stats['min'] is None else min(stats['min'], float(values.min()))
        stats['max'] = float(values.max()) if stats['max'] is None else max(stats['max'], float(values.max()))
        stats['sum'] += float(values.sum())
        stats['negative'] += int((values < 0).sum())
        if variable.name in BOUNDS and active:
            low, high = BOUNDS[variable.name]
            stats['out_of_bounds'] += int(((values < low) | (values > high if high is not None else False)).sum())
    stats['mean'] = stats.pop('sum')/max(1, stats['count']-stats['missing'])
    return stats


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--summarize':
        directory = Path(sys.argv[2])
        reports = [json.loads((directory/f'{year}.json').read_text()) for year in range(1982, 1987)]
        trends = []
        for report in reports:
            year = report['year']
            fields = report['files'][f'RESTART.{year}010100_DOMAIN1']['variables']
            routing = report['files'][f'HYDRO_RST.{year}-01-01_00:00_DOMAIN1']['variables']
            trends.append({'year': year, 'state_means': {k: fields[k]['mean'] for k in ('SOIL_T', 'SMC', 'SH2O', 'SNEQV', 'SNOWH', 'TG', 'ZWT')},
                           'routing_inventory': {k: routing[k] for k in ('qlink1', 'qlink2', 'hlink', 'z_gwsubbas')}})
        summary = {'status': 'needs_review' if any(r['warnings'] for r in reports) else 'screening_passed',
                   'warnings': {str(r['year']): r['warnings'] for r in reports}, 'yearly_summaries': trends,
                   'note': 'Year-to-year variation includes different weather; not a spin-up convergence test.'}
        (directory/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps(summary), flush=True)
        return
    root = Path(__file__).resolve().parents[1]
    year = 1982+int(os.environ['SLURM_ARRAY_TASK_ID'])
    base = root/'nwm/restarts/conus/retro'/str(year)/'01'
    destination = root/'nwm/status/spinup-restart-audit'/f"job_{os.environ['SLURM_ARRAY_JOB_ID']}"
    destination.mkdir(parents=True, exist_ok=True)
    with Dataset(root/'nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc') as domain:
        land = np.asarray(domain['XLAND'][0]) == 1
    report = {'year': year, 'active_land_cells': int(land.sum()), 'files': {}, 'warnings': []}
    expected = f'{year}-01-01_00:00:00'
    for filename in (f'RESTART.{year}010100_DOMAIN1', f'HYDRO_RST.{year}-01-01_00:00_DOMAIN1'):
        path = base/filename
        before = path.stat()
        with Dataset(path) as data:
            data.set_auto_maskandscale(False)
            stamp = str(chartostring(data['Times'][:])[0]) if 'Times' in data.variables else data.Restart_Time
            if stamp != expected:
                raise ValueError(f'Unexpected restart timestamp: {filename}/{stamp}')
            variables = {}
            for name, var in data.variables.items():
                if not np.issubdtype(var.dtype, np.number):
                    continue
                variables[name] = inventory(var, land)
                if name in BOUNDS and (variables[name]['missing'] or variables[name]['out_of_bounds']):
                    report['warnings'].append({'file': filename, 'variable': name, 'counts': variables[name]})
            if 'SMC' in data.variables:
                violations = 0
                for y in range(0, land.shape[0], 64):
                    total = np.asarray(data['SMC'][0, y:y+64, :, :])
                    liquid = np.asarray(data['SH2O'][0, y:y+64, :, :])
                    violations += int(((liquid > total+1e-6) & land[y:y+64, None, :]).sum())
                report['liquid_exceeds_total_soil_water'] = violations
                if violations:
                    report['warnings'].append({'soil_water_consistency_violations': violations})
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Restart changed during audit')
        report['files'][filename] = {'timestamp': stamp, 'bytes': after.st_size,
                                    'identity': [after.st_ino, after.st_size, after.st_mtime_ns],
                                    'variables': variables}
    report['status'] = 'needs_review' if report['warnings'] else 'screening_passed'
    report['note'] = 'Not a convergence test; inactive routing and unused snow/lake fields inventoried without physical pass/fail.'
    (destination/f'{year}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'year': year, 'status': report['status'], 'warnings': report['warnings']}), flush=True)


if __name__ == '__main__':
    main()
