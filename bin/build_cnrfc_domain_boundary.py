"""Copy CNRFC boundary sources, dissolve their union, and add a metric 20-km buffer.

Run with the base Miniforge Python (GDAL, Shapely, pyproj, matplotlib).
Creates a new output directory only; never overwrites accepted domain assets.
"""
import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from osgeo import ogr, osr
from pyproj import CRS, Proj, Transformer
from shapely import from_wkb, make_valid
from shapely.geometry import MultiPolygon
from shapely.ops import transform, unary_union


def polygonal(geometry):
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if hasattr(geometry, 'geoms'):
        return [p for g in geometry.geoms for p in polygonal(g)]
    return []


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path, layer_name, target):
    ds = ogr.Open(str(path))
    layer = ds.GetLayerByName(layer_name)
    source = CRS.from_wkt(layer.GetSpatialRef().ExportToWkt())
    project = Transformer.from_crs(source, target, always_xy=True).transform
    shapes, repaired = [], 0
    for feature in layer:
        g = from_wkb(bytes(feature.GetGeometryRef().ExportToWkb()))
        if not g.is_valid:
            repaired += 1
            g = make_valid(g)
        g = transform(project, g)
        shapes.extend(polygonal(make_valid(g)))
    result = unary_union(shapes)
    return result, {'features': layer.GetFeatureCount(), 'source_crs': source.to_string(),
                    'invalid_source_features_repaired': repaired}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--output', type=Path, default=root / 'nwm/static/domains/cnrfc/boundaries')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_paths = {
        'official': root / 'forcing/static/noaa/rfc_boundaries/20240112/cnrfc.gpkg',
        'forecast_basins': Path('/cw3e/mead/projects/cnt107/nrt_hydro/wrf_hydro/cnrfc/domain/cn_fcst_basins/CNRFC_Basins.gpkg'),
    }
    copies = {}
    for key, path in source_paths.items():
        destination = out / ('official_cnrfc.gpkg' if key == 'official' else 'CNRFC_Basins.gpkg')
        shutil.copy2(path, destination)
        assert digest(path) == digest(destination)
        copies[key] = destination
    # Local AEQD keeps distance distortion small across this north-south domain.
    metric = CRS.from_proj4('+proj=aeqd +lat_0=38 +lon_0=-119 +datum=WGS84 +units=m +no_defs')
    official, official_meta = read(copies['official'], 'cnrfc', metric)
    basins, basins_meta = read(copies['forecast_basins'], 'CNRFC_Basins', metric)
    union = unary_union([official, basins])
    buffered = union.buffer(20000, quad_segs=64)
    added = basins.difference(official)
    geometries = {'official_cnrfc': official, 'forecast_basins_dissolved': basins,
                  'basin_additions': added, 'cnrfc_union': union, 'cnrfc_union_buffer_20km': buffered}
    for name, g in geometries.items():
        assert g.is_valid and not g.is_empty, name
    assert official.difference(union).area < 1
    assert basins.difference(union).area < 1
    assert union.difference(buffered).area < 1
    to_lonlat = Transformer.from_crs(metric, 4326, always_xy=True).transform
    gpkg = out / 'cnrfc_domain_boundaries.gpkg'
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(gpkg))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    summaries = {}
    for name, g in geometries.items():
        geo = transform(to_lonlat, g)
        output_repaired = not geo.is_valid
        if output_repaired:
            geo = unary_union(polygonal(make_valid(geo)))
        assert geo.is_valid and not geo.is_empty
        layer = ds.CreateLayer(name, srs, ogr.wkbMultiPolygon)
        layer.CreateField(ogr.FieldDefn('area_km2', ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn('buffer_m', ogr.OFTReal))
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetField('area_km2', g.area / 1e6)
        feature.SetField('buffer_m', 20000 if name.endswith('20km') else 0)
        feature.SetGeometry(ogr.CreateGeometryFromWkb(MultiPolygon(polygonal(geo)).wkb))
        if layer.CreateFeature(feature) != 0:
            raise RuntimeError(f'Cannot write layer {name}')
        summaries[name] = {'area_km2_projected': g.area / 1e6, 'bounds_lonlat': geo.bounds,
                           'polygon_parts': len(polygonal(geo)), 'valid': geo.is_valid,
                           'post_reprojection_geometry_repaired': output_repaired}
    ds = None
    check = ogr.Open(str(gpkg))
    assert check.GetLayerCount() == len(geometries)
    for i in range(check.GetLayerCount()):
        layer = check.GetLayer(i)
        assert layer.GetFeatureCount() == 1
        feature = layer.GetNextFeature()
        assert from_wkb(bytes(feature.GetGeometryRef().ExportToWkb())).is_valid
    projection = Proj(metric)
    lo, la, hi, ha = summaries['cnrfc_union_buffer_20km']['bounds_lonlat']
    scales = [projection.get_factors(lo + (hi-lo)*i/10, la + (ha-la)*j/10).tissot_semimajor
              for i in range(11) for j in range(11)]
    report = {'created_utc': datetime.now(UTC).isoformat(), 'status': 'passed',
              'sources': {k: {'original': str(v), 'local_copy': copies[k].name, 'sha256': digest(copies[k])}
                          for k, v in source_paths.items()},
              'source_inventory': {'official': official_meta, 'forecast_basins': basins_meta},
              'operation': 'Dissolve official + all forecast basin polygons; outward 20000-m planar buffer',
              'buffer_crs': metric.to_wkt(), 'output_crs': 'EPSG:4326', 'buffer_quad_segs': 64,
              'sampled_max_linear_scale': max(scales), 'layers': summaries,
              'gpkg_sha256': digest(gpkg), 'model_mask_modified': False,
              'caveat': 'Projected metric buffer, not an exact geodesic buffer. Geometry union does not prove routing-network closure.'}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    fig, axes = plt.subplots(1, 2, figsize=(13, 8))
    for ax in axes:
        for name, color in [('cnrfc_union_buffer_20km', '#d9e9f2'), ('official_cnrfc', '#badbd2'), ('basin_additions', '#e7a03d')]:
            for p in polygonal(transform(to_lonlat, geometries[name])):
                ax.fill(*p.exterior.xy, color=color, linewidth=0)
                for ring in p.interiors:
                    ax.fill(*ring.xy, color='white', linewidth=0)
        for p in polygonal(transform(to_lonlat, union)):
            ax.plot(*p.exterior.xy, color='#245562', linewidth=.6)
        ax.set_aspect(1 / .8)
        ax.grid(alpha=.2); ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude')
    axes[0].set_title('CNRFC union + 20-km buffer')
    axes[1].set_xlim(-118, -115); axes[1].set_ylim(31.95, 33.5)
    axes[1].set_title('Southern extension (boundary geometry only)')
    fig.legend(handles=[Patch(color='#badbd2',label='Official CNRFC'), Patch(color='#e7a03d',label='Forecast basins outside official boundary'), Patch(color='#d9e9f2',label='20-km buffer')],loc='lower center', ncol=3)
    fig.tight_layout(rect=(0,.06,1,1)); fig.savefig(out / 'cnrfc_union_buffer_20km.png', dpi=170)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
