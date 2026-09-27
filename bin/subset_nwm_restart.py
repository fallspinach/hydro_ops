"""Exact restart cropping for coupled, no-lake UDMP WRF-Hydro 5.4 runs.

Reach arrays, including UDMP groundwater storage, follow RouteLink row order,
not sorted feature IDs or GWBUCKPARM row order. No counters or states are reset.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.nwm_masks import load_masks


def reach_indices(parent, child):
    if len(np.unique(parent)) != len(parent) or len(np.unique(child)) != len(child):
        raise ValueError('Duplicate reach IDs')
    order = np.argsort(parent)
    positions = np.searchsorted(parent[order], child)
    if np.any(positions >= len(parent)) or not np.array_equal(parent[order[positions]], child):
        raise ValueError('Subset reach IDs missing in parent')
    return order[positions]


def copy_restart(source, target, selections, lengths):
    """Copy raw values in bounded row blocks and verify every copied value."""
    if target.exists():
        raise FileExistsError(target)
    partial = target.with_name(target.name + '.partial')
    with Dataset(source) as src, Dataset(partial, 'w', format='NETCDF4') as dst:
        src.set_auto_maskandscale(False)
        dst.setncatts({key: src.getncattr(key) for key in src.ncattrs()})
        for name, dim in src.dimensions.items():
            size = lengths.get(name, len(dim))
            dst.createDimension(name, None if dim.isunlimited() else size)
        for name, var in src.variables.items():
            kwargs = {'fill_value': var.getncattr('_FillValue')} if '_FillValue' in var.ncattrs() else {}
            out = dst.createVariable(name, var.datatype, var.dimensions,
                                     zlib=bool(var.ndim), complevel=2, **kwargs)
            out.setncatts({key: var.getncattr(key) for key in var.ncattrs() if key != '_FillValue'})
            out.set_auto_maskandscale(False)
            block_axis = next((i for i, dim in enumerate(var.dimensions)
                               if dim in ('south_north', 'south_north_stag', 'iy', 'iyrt')), None)
            count = lengths[var.dimensions[block_axis]] if block_axis is not None else 1
            for first in range(0, count, 128):
                read = [selections.get(dim, slice(None)) for dim in var.dimensions]
                write = [slice(None)] * var.ndim
                if block_axis is not None:
                    stop = min(first + 128, count)
                    offset = read[block_axis].start
                    read[block_axis] = slice(offset + first, offset + stop)
                    write[block_axis] = slice(first, stop)
                values = var[tuple(read)]
                out[tuple(write)] = values
                np.testing.assert_array_equal(out[tuple(write)], values)
    # Second open independently validates the finished file, including attributes.
    with Dataset(source) as src, Dataset(partial) as dst:
        src.set_auto_maskandscale(False)
        dst.set_auto_maskandscale(False)
        for key in src.ncattrs():
            np.testing.assert_array_equal(src.getncattr(key), dst.getncattr(key))
        for name, var in src.variables.items():
            out = dst[name]
            axis = next((i for i, dim in enumerate(var.dimensions)
                         if dim in ('south_north', 'south_north_stag', 'iy', 'iyrt')), None)
            count = out.shape[axis] if axis is not None else 1
            for first in range(0, count, 128):
                read = [selections.get(dim, slice(None)) for dim in var.dimensions]
                write = [slice(None)] * var.ndim
                if axis is not None:
                    stop = min(first + 128, count)
                    offset = read[axis].start
                    read[axis], write[axis] = slice(offset + first, offset + stop), slice(first, stop)
                np.testing.assert_array_equal(out[tuple(write)], var[tuple(read)])
    partial.replace(target)


def subset_pair(land, hydro, parent_routes, parameters, output):
    if output.exists():
        raise FileExistsError(output)
    window, _, _, _ = load_masks(parameters / 'domain_masks.nc')
    with Dataset(parent_routes) as data:
        parent = np.asarray(data['link'][:])
    with Dataset(parameters / 'RouteLink.nc') as data:
        child = np.asarray(data['link'][:])
    indices = reach_indices(parent, child)
    land_sel, hydro_sel = {}, {'links': indices}
    for x, y, factor in [('west_east', 'south_north', 1), ('west_east_stag', 'south_north_stag', 1),
                          ('ix', 'iy', 1), ('ixrt', 'iyrt', 4)]:
        extra = int(x.endswith('_stag'))
        mapping = land_sel if x.startswith('west') else hydro_sel
        mapping[x] = slice(window.west_east_start * factor, (window.west_east_end + 1) * factor + extra)
        mapping[y] = slice(window.south_north_start * factor, (window.south_north_end + 1) * factor + extra)
    with Dataset(hydro) as data:
        if (len(data.dimensions['links']) != len(parent) or data['z_gwsubbas'].dimensions != ('links',)
                or 'resht' in data.variables or getattr(data, 'channel_only', 0) != 0):
            raise ValueError('Unsupported restart: require coupled UDMP no-lake schema')
        if any('basns' in var.dimensions for var in data.variables.values()):
            raise ValueError('Basin-indexed state needs a separate mapping')
        if len(data.dimensions['ixrt']) != 4 * len(data.dimensions['ix']):
            raise ValueError('Unexpected routing grid ratio')
    paths = [land, hydro, parent_routes, parameters / 'RouteLink.nc', parameters / 'domain_masks.nc']
    before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    output.mkdir(parents=True)
    for path, selections in [(land, land_sel), (hydro, hydro_sel)]:
        lengths = {key: value.stop - value.start if isinstance(value, slice) else len(value)
                   for key, value in selections.items()}
        if path == hydro:
            lengths['basns'] = len(child)
        copy_restart(path, output / path.name, selections, lengths)
    if before != [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]:
        raise ValueError('Source restart or mapping changed during extraction')
    report = {'status': 'passed', 'source_land': str(land), 'source_hydro': str(hydro),
              'parent_routes': str(parent_routes), 'subset_parameters': str(parameters),
              'window': window.__dict__, 'reach_count': len(child),
              'reach_order': 'subset RouteLink row order mapped by link ID',
              'all_selected_values_exact': True, 'timestamps_and_counters_unchanged': True}
    (output / 'subset_restart.json').write_text(json.dumps(report, indent=2) + '\n')
    return output / land.name, output / hydro.name


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('land', 'hydro', 'parent-routes', 'parameters', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    subset_pair(args.land, args.hydro, args.parent_routes, args.parameters, args.output)
