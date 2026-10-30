"""Build an isolated official-CNRFC union HU2-18 candidate, buffered 20 km.

Run on a compute node using base Miniforge (GDAL/GeoPandas/netCDF4).
Never overwrite an existing candidate or change the operational CNRFC domain.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests
from build_cnrfc_domain_boundary import digest, polygonal
from pyproj import CRS, Proj, Transformer
from shapely import make_valid
from shapely.ops import transform, unary_union

from hydro_ops.nwm_masks import load_masks

URL = 'https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/WBD/HU2/GDB/WBD_18_HU2_GDB.zip'
METRIC = CRS.from_proj4('+proj=aeqd +lat_0=38 +lon_0=-119 +datum=WGS84 +units=m +no_defs')


def dissolve(frame):
    repaired = int((~frame.geometry.is_valid).sum())
    parts = [part for geometry in frame.geometry for part in polygonal(make_valid(geometry))]
    geometry = unary_union(parts)
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError('Invalid/empty dissolved polygon')
    return geometry, repaired


def build_geometries(official, hu18):
    union = unary_union([official, hu18])
    buffered = union.buffer(20000, quad_segs=64)
    for geometry in (union, buffered):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError('Invalid union/buffer')
    if official.difference(union).area > 1 or hu18.difference(union).area > 1:
        raise ValueError('Union does not contain both sources')
    if union.difference(buffered).area > 1:
        raise ValueError('Buffer does not contain union')
    return union, buffered


def extract_archive(archive, destination):
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path = (destination / info.filename).resolve()
            if not path.is_relative_to(destination.resolve()) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe ZIP member')
        z.extractall(destination)


def mask_changes(old_path, new_path):
    old_window, old, _, _ = load_masks(old_path)
    new_window, new, _, _ = load_masks(new_path)
    # CONUS index coordinates, independent of changed rectangular crop dimensions.
    def indices(window, mask):
        y, x = np.nonzero(mask)
        return (y.astype('i8') + window.south_north_start) * 100000 + x + window.west_east_start
    changes = {}
    for name in old:
        before, after = indices(old_window, old[name]), indices(new_window, new[name])
        shared = np.intersect1d(before, after, assume_unique=True).size
        changes[name] = {'old_cells': int(before.size), 'new_cells': int(after.size),
                         'added_cells': int(after.size-shared), 'removed_cells': int(before.size-shared)}
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New candidate domain root')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from hydro_ops.cnrfc_domain import require_legacy_grid
    require_legacy_grid(root)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    boundaries = out / 'boundaries'
    boundaries.mkdir()
    sources = boundaries / 'sources'
    sources.mkdir()
    report = {'status': 'running', 'created_utc': datetime.now(UTC).isoformat(),
              'job_id': os.environ.get('SLURM_JOB_ID'),
              'definition': '(official CNRFC union WBD HU2 18) buffered outward 20000 m',
              'reporting_region': 'official CNRFC union WBD HU2 18; forecast basins are comparison only',
              'operational_domain_modified': False, 'parameters_extracted': False}
    try:
        archive = sources / 'WBD_18_HU2_GDB.zip'
        with requests.get(URL, stream=True, timeout=(30, 300)) as response:
            response.raise_for_status()
            report['wbd_download'] = {'url': URL, 'last_modified': response.headers.get('Last-Modified'),
                                      'etag': response.headers.get('ETag')}
            with archive.open('xb') as stream:
                for chunk in response.iter_content(1024*1024):
                    stream.write(chunk)
        report['wbd_download']['sha256'] = digest(archive)
        extract_archive(archive, sources)
        databases = list(sources.rglob('*.gdb'))
        if len(databases) != 1:
            raise ValueError('Expected exactly one WBD geodatabase')
        layers = gpd.list_layers(databases[0])
        layer = next(name for name in layers.name if name.lower() == 'wbdhu2')
        wbd = gpd.read_file(databases[0], layer=layer)
        field = next(name for name in wbd.columns if name.lower() == 'huc2')
        wbd = wbd.loc[wbd[field].astype(str).str.zfill(2) == '18']
        if len(wbd) != 1:
            raise ValueError('Expected exactly one HU2 18 feature')
        report['wbd_feature'] = {str(k): str(v) for k, v in wbd.drop(columns='geometry').iloc[0].items()}
        official_source = root / 'nwm/static/domains/cnrfc/boundaries/official_cnrfc.gpkg'
        official_copy = sources / 'official_cnrfc.gpkg'
        shutil.copy2(official_source, official_copy)
        report['official_source'] = {'path': str(official_source.relative_to(root)), 'sha256': digest(official_copy)}
        official_frame = gpd.read_file(official_copy).to_crs(METRIC)
        # Repair in native CRS before projection, then validate in the metric CRS.
        native_invalid = int((~wbd.geometry.is_valid).sum())
        wbd.geometry = wbd.geometry.map(make_valid)
        hu18, hu_repaired = dissolve(wbd.to_crs(METRIC))
        official, official_repaired = dissolve(official_frame)
        union, buffered = build_geometries(official, hu18)
        old_path = root / 'nwm/static/domains/cnrfc/boundaries/cnrfc_domain_boundaries.gpkg'
        old_union, _ = dissolve(gpd.read_file(old_path, layer='cnrfc_union').to_crs(METRIC))
        old_buffer, _ = dissolve(gpd.read_file(old_path, layer='cnrfc_union_buffer_20km').to_crs(METRIC))
        geometries = {'official_cnrfc': official, 'wbd_hu2_18': hu18,
                      'cnrfc_hu18_union': union, 'cnrfc_hu18_buffer_20km': buffered,
                      'added_reporting_area': union.difference(old_union),
                      'removed_reporting_area': old_union.difference(union),
                      'previous_buffer_20km': old_buffer}
        gpkg = boundaries / 'cnrfc_hu18_boundaries.gpkg'
        equal_area = Transformer.from_crs(METRIC, 5070, always_xy=True).transform
        summaries = {}
        for name, geometry in geometries.items():
            frame = gpd.GeoDataFrame({'name': [name]}, geometry=[geometry], crs=METRIC).to_crs(4326)
            frame.geometry = frame.geometry.map(lambda g: unary_union(polygonal(make_valid(g))))
            if not frame.geometry.is_valid.all():
                raise ValueError(f'Invalid output geometry: {name}')
            frame.to_file(gpkg, layer=name, driver='GPKG')
            summaries[name] = {'area_km2_equal_area': transform(equal_area, geometry).area/1e6,
                               'bounds_lonlat': list(frame.total_bounds), 'valid': True}
        report.update(layers=summaries, buffer_crs=METRIC.to_wkt(), buffer_distance_m=20000,
                      buffer_quad_segs=64, output_crs='EPSG:4326',
                      buffer_caveat='Local projected buffer, not exact geodesic distance; no routing closure guarantee',
                      geometry_repairs={'wbd_native': native_invalid, 'wbd_projected': hu_repaired,
                                        'official_projected': official_repaired}, gpkg_sha256=digest(gpkg))
        lo, la, hi, ha = summaries['cnrfc_hu18_buffer_20km']['bounds_lonlat']
        projection = Proj(METRIC)
        report['sampled_max_linear_scale'] = max(projection.get_factors(
            lo+(hi-lo)*i/10, la+(ha-la)*j/10).tissot_semimajor for i in range(11) for j in range(11))
        # Shared existing grid/mask builder checks parent coordinate agreement,
        # envelope checksum, and active-model-cell containment in forcing coverage.
        mask = out / 'masks/cnrfc_hu18_masks.nc'
        subprocess.run([sys.executable, str(root/'bin/build_nwm_domain_masks.py'),
                        '--boundary', str(gpkg), '--layer', 'cnrfc_hu18_buffer_20km',
                        '--output', str(mask)], check=True)
        report['masks'] = json.loads(mask.with_suffix('.json').read_text())
        report['mask_sha256'] = digest(mask)
        report['changes_from_operational_masks'] = mask_changes(
            root/'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc', mask)
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
        fig, axes = plt.subplots(1, 2, figsize=(14, 9))
        for ax in axes:
            for name, color in [('cnrfc_hu18_buffer_20km', '#d6e7f0'), ('cnrfc_hu18_union', '#a8cfc2'),
                                ('added_reporting_area', '#edac45'), ('removed_reporting_area', '#d54164')]:
                gpd.read_file(gpkg, layer=name).plot(ax=ax, color=color, linewidth=0)
            gpd.read_file(gpkg, layer='previous_buffer_20km').boundary.plot(ax=ax, color='#4e5260', linewidth=.7)
            ax.grid(alpha=.2); ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude')
        axes[0].set_title('Official CNRFC + WBD HU2 18 + 20 km')
        axes[1].set_xlim(-118, -114.5); axes[1].set_ylim(31.8, 33.5)
        axes[1].set_title('Southern extension')
        fig.legend(handles=[Patch(color=c,label=l) for c,l in [('#d6e7f0','New buffer'),
            ('#a8cfc2','Reporting region'),('#edac45','Added reporting area'),
            ('#d54164','Omitted old reporting area'),('#4e5260','Old buffered boundary (line)')]],
            loc='lower center', ncol=3)
        fig.tight_layout(rect=(0,.09,1,1))
        fig.savefig(boundaries/'cnrfc_hu18_comparison.png', dpi=170)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        (out/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
