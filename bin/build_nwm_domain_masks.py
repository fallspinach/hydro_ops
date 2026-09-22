"""Rasterize a buffered domain and intersect separate CONUS model/forcing masks.

Requires GDAL, Shapely, pyproj and netCDF4 (available in base Miniforge).
"""
import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from osgeo import ogr
from pyproj import CRS, Transformer
from shapely import from_wkb, intersects_xy, prepare
from shapely.ops import transform, unary_union

from hydro_ops.nwm_masks import derive_masks, load_masks, mask_digest
from hydro_ops.nwm_subset import GridWindow


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boundary', type=Path, required=True)
    parser.add_argument('--layer', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wrfinput', type=Path, default=root / 'nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc')
    parser.add_argument('--envelope', type=Path, default=root / 'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    ds = ogr.Open(str(args.boundary))
    layer = ds.GetLayerByName(args.layer)
    project = Transformer.from_crs(CRS.from_wkt(layer.GetSpatialRef().ExportToWkt()), 4326, always_xy=True).transform
    boundary = unary_union([transform(project, from_wkb(bytes(f.GetGeometryRef().ExportToWkb()))) for f in layer])
    if not boundary.is_valid or boundary.is_empty:
        raise ValueError('Boundary must be valid and nonempty')
    prepare(boundary)
    with Dataset(args.envelope) as env, Dataset(args.wrfinput) as model:
        shape = env['keep'].shape
        inside = np.zeros(shape, bool)
        active = np.zeros(shape, bool)
        keep = np.zeros(shape, bool)
        for y in range(0, shape[0], 128):
            sl = slice(y, y+128)
            lat, lon = np.asarray(env['lat'][sl]), np.asarray(env['lon'][sl])
            np.testing.assert_allclose(lat, model['XLAT'][0, sl], rtol=0, atol=1e-5)
            np.testing.assert_allclose(lon, model['XLONG'][0, sl], rtol=0, atol=1e-5)
            inside[sl] = intersects_xy(boundary, lon, lat)
            active[sl] = np.asarray(model['XLAND'][0, sl]) == 1
            keep[sl] = np.asarray(env['keep'][sl], bool)
        if mask_digest(keep) != env.keep_sha256:
            raise ValueError('CONUS envelope checksum mismatch')
        model_mask, forcing_mask = derive_masks(inside, active, keep)
        yy, xx = np.where(inside)
        window = GridWindow(int(xx.min()), int(xx.max()), int(yy.min()), int(yy.max()))
        slices = (slice(window.south_north_start, window.south_north_end+1),
                  slice(window.west_east_start, window.west_east_end+1))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        partial = args.output.with_name(args.output.name+'.partial')
        with Dataset(partial, 'w', format='NETCDF4') as out:
            out.createDimension('y', window.shape[0]); out.createDimension('x', window.shape[1])
            for name in ('lat', 'lon'):
                v = out.createVariable(name, 'f4', ('y', 'x'), zlib=True, complevel=2)
                v[:] = env[name][slices]
                v.units = 'degrees_north' if name == 'lat' else 'degrees_east'
            for name, values in [('boundary_mask', inside), ('model_mask', model_mask), ('forcing_mask', forcing_mask)]:
                v = out.createVariable(name, 'u1', ('y', 'x'), zlib=True, complevel=2)
                v[:] = values[slices]; v.sha256 = mask_digest(values[slices])
                v.flag_values = np.array([0, 1], 'u1'); v.flag_meanings = 'excluded included'
            out.source_window = json.dumps(window.__dict__)
            out.source_grid_shape = json.dumps(shape)
            out.boundary_source = str(args.boundary.resolve()); out.boundary_layer = args.layer
            out.wrfinput_source = str(args.wrfinput.resolve()); out.forcing_envelope = str(args.envelope.resolve())
            out.forcing_envelope_sha256 = env.keep_sha256
            out.rasterization_rule = 'grid-cell centers intersect buffered polygon; no extra raster buffer'
            out.model_rule = 'boundary_mask AND source XLAND==1'
            out.forcing_rule = 'boundary_mask AND CONUS static envelope keep'
            out.routing_note = 'model_mask is land-surface activity, not a channel/network deletion mask'
        load_masks(partial)
        partial.replace(args.output)
        report = {'status': 'passed', 'mask_file': str(args.output.resolve()),
                  'one_km_window': window.__dict__, 'one_km_shape': window.shape,
                  'routing_window_factor4': window.refined(4).__dict__,
                  'boundary_cells': int(inside.sum()), 'active_model_cells': int(model_mask.sum()),
                  'forcing_cells': int(forcing_mask.sum()),
                  'extra_forcing_cells': int((forcing_mask & ~model_mask).sum()),
                  'active_without_forcing': int((model_mask & ~forcing_mask).sum()),
                  'boundary_bounds_lonlat': boundary.bounds,
                  'containment_rule': 'cell centers; 20-km buffer already included',
                  'model_parameters_modified': False}
        args.output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
