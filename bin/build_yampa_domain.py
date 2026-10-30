"""Union four requested Yampa HU8 regions and build 20-km support masks."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hydro_ops.wrf_hydro.production import write_json

REGIONS = {'14050001': 'Upper Yampa', '14050002': 'Lower Yampa',
           '14050003': 'Little Snake', '14050004': 'Muddy'}


def select_regions(frame):
    fields = {c.lower(): c for c in frame.columns}
    codes = frame[fields['huc8']].astype(str).str.zfill(8)
    selected = frame.loc[codes.isin(REGIONS)].copy()
    if len(selected) != 4 or set(selected[fields['huc8']].astype(str)) != set(REGIONS):
        raise ValueError('Expected exactly one feature for each of the four requested HU8 codes')
    for _, row in selected.iterrows():
        if row[fields['name']].strip().casefold() != REGIONS[str(row[fields['huc8']])].casefold():
            raise ValueError('HU8 code/name mismatch')
    return selected


def main():
    import geopandas as gpd
    from build_cnrfc_hu18_domain import dissolve, extract_archive
    from prepare_b120_domain import digest
    from pyproj import CRS
    from shapely import make_valid

    from hydro_ops.nwm_masks import load_masks

    root = Path(__file__).resolve().parents[1]
    parent = root / 'nwm/static/domains/ucrb'
    source = parent / 'boundaries/sources/WBD_14_HU2_GDB.zip'
    provenance = json.loads((parent / 'manifest.json').read_text())['source']
    if digest(source) != provenance['sha256']:
        raise ValueError('Preserved WBD source checksum mismatch')
    out = root / 'nwm/static/domains/yampa'
    out.mkdir(parents=True, exist_ok=False)
    originals = out / 'boundaries/sources'
    originals.mkdir(parents=True)
    report = {'status': 'running', 'domain': 'yampa', 'job_id': os.environ['SLURM_JOB_ID'],
              'approved_for_production': False, 'parameters_extracted': False,
              'model_smoke_tested': False, 'requested_hu8': REGIONS,
              'definition': 'Union of four requested HU8 regions plus a single 20000 m buffer'}
    try:
        archive = originals / source.name
        shutil.copy2(source, archive)
        if digest(archive) != provenance['sha256']:
            raise ValueError('Copied WBD checksum mismatch')
        report['source'] = provenance | {'copied_from': str(source)}
        scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}/yampa-wbd')
        scratch.mkdir(parents=True, exist_ok=False)
        extract_archive(archive, scratch)
        databases = list(scratch.rglob('*.gdb'))
        if len(databases) != 1:
            raise ValueError('Expected one geodatabase')
        layer = next(n for n in gpd.list_layers(databases[0]).name if n.lower() == 'wbdhu8')
        selected = select_regions(gpd.read_file(databases[0], layer=layer))
        if selected.crs is None or selected.geometry.isna().any() or selected.is_empty.any():
            raise ValueError('Missing CRS or empty geometry')
        report['source_features'] = [{str(k): str(v) for k, v in row.items()}
                                     for row in selected.drop(columns='geometry').to_dict('records')]
        report['native_invalid_repaired'] = int((~selected.is_valid).sum())
        selected.geometry = selected.geometry.map(make_valid)
        # Use the same local metric CRS as UCRB, maintaining buffer consistency.
        metric = CRS.from_proj4('+proj=aeqd +lat_0=39 +lon_0=-109 +datum=WGS84 +units=m +no_defs')
        region, repaired = dissolve(selected.to_crs(metric))
        buffered = region.buffer(20000, quad_segs=64)
        if buffered.is_empty or not buffered.is_valid or region.difference(buffered).area > 1:
            raise ValueError('Invalid buffer or containment')
        gpkg = out / 'boundaries/yampa_boundaries.gpkg'
        selected.to_crs(4326).to_file(gpkg, layer='wbd_hu8_selected', driver='GPKG')
        areas = {}
        for name, geometry in [('yampa_reporting', region), ('yampa_buffer_20km', buffered)]:
            frame = gpd.GeoDataFrame({'name': [name]}, geometry=[geometry], crs=metric).to_crs(4326)
            frame.to_file(gpkg, layer=name, driver='GPKG')
            areas[name] = {'area_km2_equal_area': float(frame.to_crs(5070).area.sum() / 1e6),
                           'bounds_lonlat': list(frame.total_bounds)}
        report.update(layers=areas, reporting_layer='yampa_reporting', support_layer='yampa_buffer_20km',
                      boundary_sha256=digest(gpkg), buffer_crs=metric.to_wkt(),
                      buffer_distance_m=20000, projected_repairs=repaired,
                      buffer_caveat='Local projected distance, not exact geodesic; routing closure not guaranteed')
        mask = out / 'masks/yampa_masks.nc'
        subprocess.run([sys.executable, str(root / 'bin/build_nwm_domain_masks.py'),
                        '--boundary', str(gpkg), '--layer', 'yampa_buffer_20km',
                        '--output', str(mask)], check=True)
        report.update(masks=json.loads(mask.with_suffix('.json').read_text()), mask_sha256=digest(mask))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        _, masks, lat, lon = load_masks(mask)
        fig, axes = plt.subplots(1, 2, figsize=(14, 8))
        for ax in axes:
            gpd.read_file(gpkg, layer='yampa_buffer_20km').plot(ax=ax, color='#dce9f1', edgecolor='#7d97a6')
            gpd.read_file(gpkg, layer='yampa_reporting').plot(ax=ax, color='#c6dfd1', edgecolor='#15573f')
            ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude'); ax.grid(alpha=.2)
        native = selected.to_crs(4326)
        native.boundary.plot(ax=axes[0], color='#55816d', linewidth=.7)
        fields = {c.lower(): c for c in selected.columns}
        for _, row in native.iterrows():
            point = row.geometry.representative_point()
            axes[0].annotate(f'{row[fields["name"]]}\n{row[fields["huc8"]]}',
                             (point.x, point.y), ha='center', fontsize=8)
        axes[0].set_title('Yampa: four HU8 regions + 20-km buffer')
        axes[1].contour(lon, lat, masks['forcing_mask'], levels=[.5], colors=['#dc8d18'], linewidths=1)
        axes[1].contour(lon, lat, masks['model_mask'], levels=[.5], colors=['#245cad'], linewidths=.5)
        axes[1].set_title('Aligned model and forcing masks')
        fig.legend(handles=[Patch(color='#c6dfd1', label='Reporting union'),
            Patch(color='#dce9f1', label='20-km support buffer'),
            Line2D([], [], color='#dc8d18', label='Forcing mask'),
            Line2D([], [], color='#245cad', label='Model mask')], loc='lower center', ncol=4)
        fig.tight_layout(rect=(0, .06, 1, 1))
        fig.savefig(out / 'boundaries/yampa_domain.png', dpi=160, bbox_inches='tight')
        plt.close(fig)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(out / 'manifest.json', report)


if __name__ == '__main__':
    main()
