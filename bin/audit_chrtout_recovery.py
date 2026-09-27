"""Read-only inventory of channel archive shapes and recoverable restart pairs."""
import json
import argparse
from collections import defaultdict
from pathlib import Path

from netCDF4 import Dataset, chartostring


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restarts-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / 'nwm/recovery/chrtout_record_dimension'
    destination.mkdir(parents=True, exist_ok=True)
    report = {'archives': {}, 'restarts': [], 'restart_errors': [], 'raw_hourly_candidates': []}
    for domain, campaign in [('conus', 'production_1979_v1/production'),
                             ('cnrfc', 'production_1979_1981_v1')]:
        if args.restarts_only:
            break
        base = root / 'nwm/outputs' / domain / 'retro' / campaign
        counts = defaultdict(lambda: {'files': 0, 'single_channel_array': 0,
                                      'proper_record_variables': 0, 'errors': []})
        for path in sorted((base / 'hourly').glob('*/*/*.CHRTOUT_DOMAIN1')):
            item = counts[path.name[:4]]
            item['files'] += 1
            try:
                with Dataset(path) as data:
                    dims = data['streamflow'].dimensions
                    key = 'proper_record_variables' if 'time' in dims else 'single_channel_array'
                    item[key] += 1
            except Exception as error:
                item['errors'].append({'path': str(path), 'error': str(error)})
        report['archives'][domain] = dict(counts)
    # Entire permanent restart tree, plus initialization seeds in run directories.
    land = list((root / 'nwm/restarts').rglob('RESTART.*_DOMAIN1'))
    land += list((root / 'nwm/runs/conus/retro/production_1979_v1/initialization-preserved-counter').glob('RESTART.*_DOMAIN1'))
    land += list((root / 'nwm/runs/cnrfc/retro/production_1979_1981_v1/initialization').glob('RESTART.*_DOMAIN1'))
    for path in sorted(land):
        try:
            with Dataset(path) as data:
                stamp = str(chartostring(data['Times'][:]).reshape(-1)[0])
                for name in ('SMC', 'SH2O', 'SOIL_T', 'SNEQV'):
                    if name not in data.variables:
                        raise ValueError(f'Missing land state {name}')
            hydro = path.with_name('HYDRO_RST.' + stamp[:16] + '_DOMAIN1')
            with Dataset(hydro) as data:
                if data.Restart_Time != stamp:
                    raise ValueError('Restart times differ')
                for name in ('hlink', 'qlink1', 'qlink2', 'z_gwsubbas'):
                    if name not in data.variables:
                        raise ValueError(f'Missing hydro state {name}')
            report['restarts'].append({'time': stamp, 'land': str(path), 'hydro': str(hydro),
                'land_bytes': path.stat().st_size, 'hydro_bytes': hydro.stat().st_size,
                'land_mtime_ns': path.stat().st_mtime_ns, 'hydro_mtime_ns': hydro.stat().st_mtime_ns,
                'validation': 'header/time/required variables; not full state scan'})
        except Exception as error:
            report['restart_errors'].append({'path': str(path), 'error': str(error)})
    for domain in ('conus', 'cnrfc'):
        if args.restarts_only:
            break
        for path in (root / 'nwm/outputs' / domain).rglob('*.CHRTOUT_DOMAIN1'):
            if len(path.name.split('.')[0]) > 8:
                report['raw_hourly_candidates'].append(str(path))
    filename = 'audit-restarts.json' if args.restarts_only else 'audit.json'
    partial = destination / (filename + '.part')
    partial.write_text(json.dumps(report, indent=2) + '\n')
    partial.replace(destination / filename)
    print(json.dumps({'archives': report['archives'], 'restart_pairs': len(report['restarts']),
                      'restart_errors': report['restart_errors'],
                      'raw_hourly_candidates': len(report['raw_hourly_candidates'])}), flush=True)


if __name__ == '__main__':
    main()
