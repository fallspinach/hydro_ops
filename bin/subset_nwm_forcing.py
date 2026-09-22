"""Crop a CONUS LDASIN to a shared domain window and apply its forcing mask.

No remapping, interpolation or nearest-neighbor filling. Input remains untouched.
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.nwm_masks import load_masks, mask_digest

FIELDS = ('T2D', 'Q2D', 'PSFC', 'SWDOWN', 'LWDOWN', 'U2D', 'V2D', 'RAINRATE')


def subset(source, output, mask_path):
    window, masks, lat, lon = load_masks(mask_path)
    if output.exists() or source.resolve() == output.resolve():
        raise FileExistsError('Refusing to overwrite an existing forcing file')
    ys = slice(window.south_north_start, window.south_north_end+1)
    xs = slice(window.west_east_start, window.west_east_end+1)
    with Dataset(source) as data:
        np.testing.assert_allclose(data['lat'][ys, xs], lat, rtol=0, atol=1e-5)
        np.testing.assert_allclose(data['lon'][ys, xs], lon, rtol=0, atol=1e-5)
        for name in FIELDS:
            if data[name].dimensions != ('time', 'y', 'x'):
                raise ValueError(f'Unsupported dimension order: {name}')
        times = np.asarray(data['time'][:])
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name+'.partial')
    if partial.exists():
        raise FileExistsError(partial)
    subprocess.run(['ncks', '-4', '-L', '2', '-d', f'y,{ys.start},{ys.stop-1}',
                    '-d', f'x,{xs.start},{xs.stop-1}', str(source), str(partial)], check=True)
    try:
        with Dataset(partial, 'r+') as data:
            for name in FIELDS:
                variable = data[name]
                for index in range(len(times)):
                    values = np.ma.array(variable[index], copy=True)
                    invalid = np.ma.getmaskarray(values) | ~np.isfinite(np.ma.getdata(values))
                    if np.any(invalid & masks['model_mask']):
                        raise ValueError(f'{name} hour {index}: missing active-cell forcing; no fill attempted')
                    variable[index] = np.ma.masked_where(~masks['forcing_mask'], values)
            for name in ('model_mask', 'forcing_mask'):
                v = data.createVariable('subset_'+name, 'u1', ('y', 'x'), zlib=True, complevel=2)
                v[:] = masks[name]; v.sha256 = mask_digest(masks[name])
            # CONUS audit/count metadata is retained only as parent evidence.
            for key in list(data.ncattrs()):
                if key.startswith(('forcing_domain_', 'forcing_static_mask', 'forcing_gap_fill_', 'forcing_active_mask')):
                    data.setncattr('parent_'+key, data.getncattr(key)); data.delncattr(key)
            data.subset_mask_file = str(mask_path.resolve())
            data.subset_source = str(source.resolve())
            data.subset_window = json.dumps(window.__dict__)
            data.subset_policy = 'exact grid crop; forcing_mask retention; model_mask completeness; no fill'
        with Dataset(partial) as data, Dataset(source) as original:
            np.testing.assert_array_equal(data['time'][:], times)
            for name in FIELDS:
                for index in range(len(times)):
                    expected = np.ma.masked_where(~masks['forcing_mask'], original[name][index, ys, xs])
                    got = data[name][index]
                    np.testing.assert_array_equal(np.ma.getmaskarray(got), np.ma.getmaskarray(expected))
                    np.testing.assert_array_equal(got.compressed(), expected.compressed())
        partial.replace(output)
    except BaseException as error:
        # Keep failed candidate for diagnosis; never publish it as the final file.
        partial.with_name(partial.name+'.failure.json').write_text(json.dumps(
            {'status': 'failed', 'source': str(source), 'error': str(error)}, indent=2)+'\n')
        raise
    report = {'status': 'passed', 'source': str(source.resolve()), 'output': str(output.resolve()),
              'window': window.__dict__, 'records': len(times), 'shape': window.shape,
              'active_cells': int(masks['model_mask'].sum()),
              'retained_forcing_cells': int(masks['forcing_mask'].sum()),
              'missing_active_values': 0, 'retained_values_exact_match': True}
    output.with_name(output.name+'.subset.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--domain-masks', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(subset(args.source, args.output, args.domain_masks), indent=2))


if __name__ == '__main__':
    main()
