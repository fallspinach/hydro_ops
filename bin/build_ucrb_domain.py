"""Build the UCRB HU2-14 boundary and aligned 20-km support masks for review."""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.wrf_hydro.production import write_json

URL = 'https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/WBD/HU2/GDB/WBD_14_HU2_GDB.zip'


def select_region(frame):
    fields = {name.lower(): name for name in frame.columns}
    selected = frame.loc[frame[fields['huc2']].astype(str).str.zfill(2) == '14'].copy()
    if len(selected) != 1:
        raise ValueError('Expected exactly one HU2 14 feature')
    if selected.iloc[0][fields['name']].strip().casefold() != 'upper colorado region':
        raise ValueError('HU2 14 region name mismatch')
    return selected


def main():
    import geopandas as gpd
    import requests
    from build_cnrfc_hu18_domain import dissolve, extract_archive
    from prepare_b120_domain import digest
    from pyproj import CRS, Proj
    from shapely import make_valid

    from hydro_ops.nwm_masks import load_masks

    root = Path(__file__).resolve().parents[1]
    out = root / 'nwm/static/domains/ucrb'
    out.mkdir(parents=True, exist_ok=False)
    sources = out / 'boundaries/sources'
    sources.mkdir(parents=True)
    report = {'status': 'running', 'domain': 'ucrb', 'job_id': os.environ['SLURM_JOB_ID'],
              'created_utc': datetime.now(UTC).isoformat(), 'approved_for_production': False,
              'parameters_extracted': False, 'model_smoke_tested': False,
              'definition': 'WBD HU2 14 reporting region plus 20000 m support buffer'}
    try:
        archive = sources / 'WBD_14_HU2_GDB.zip'
        with requests.get(URL, stream=True, timeout=(30, 300)) as response:
            response.raise_for_status()
            report['source'] = {'url': URL, 'etag': response.headers.get('ETag'),
                                'last_modified': response.headers.get('Last-Modified')}
            with archive.open('xb') as handle:
                for chunk in response.iter_content(1024 * 1024):
                    handle.write(chunk)
        report['source']['sha256'] = digest(archive)
        # Retain the original archive; unpack temporary geodatabase on node scratch.
        scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}/ucrb-wbd')
        scratch.mkdir(parents=True, exist_ok=False)
        extract_archive(archive, scratch)
        databases = list(scratch.rglob('*.gdb'))
        if len(databases) != 1:
            raise ValueError('Expected exactly one WBD geodatabase')
        layers = {name.lower(): name for name in gpd.list_layers(databases[0]).name}
        selected = select_region(gpd.read_file(databases[0], layer=layers['wbdhu2']))
        if selected.crs is None or selected.geometry.isna().any() or selected.is_empty.any():
            raise ValueError('Missing CRS or empty source geometry')
        report['source_feature'] = {str(k): str(v) for k, v in selected.drop(columns='geometry').iloc[0].items()}
        native_invalid = int((~selected.is_valid).sum())
        selected.geometry = selected.geometry.map(make_valid)
        # Basin-centered metre CRS, rather than the California-centered CNRFC CRS.
        metric = CRS.from_proj4('+proj=aeqd +lat_0=39 +lon_0=-109 +datum=WGS84 +units=m +no_defs')
        region, projected_repairs = dissolve(selected.to_crs(metric))
        buffered = region.buffer(20000, quad_segs=64)
        if not buffered.is_valid or buffered.is_empty or region.difference(buffered).area > 1:
            raise ValueError('Invalid buffered geometry or containment')
        gpkg = out / 'boundaries/ucrb_boundaries.gpkg'
        selected.to_crs(4326).to_file(gpkg, layer='wbd_hu2_14', driver='GPKG')
        summaries = {}
        for name, shape in [('ucrb_reporting', region), ('ucrb_buffer_20km', buffered)]:
            frame = gpd.GeoDataFrame({'name': [name]}, geometry=[shape], crs=metric).to_crs(4326)
            frame.to_file(gpkg, layer=name, driver='GPKG')
            summaries[name] = {'bounds_lonlat': list(frame.total_bounds),
                               'area_km2_equal_area': float(frame.to_crs(5070).area.sum() / 1e6)}
        if 'wbdhu4' in layers:
            subregions = gpd.read_file(databases[0], layer=layers['wbdhu4'])
            field = next(c for c in subregions.columns if c.lower() == 'huc4')
            subregions = subregions.loc[subregions[field].astype(str).str.startswith('14')]
            subregions.to_crs(4326).to_file(gpkg, layer='wbd_hu4_context', driver='GPKG')
        lo, la, hi, ha = summaries['ucrb_buffer_20km']['bounds_lonlat']
        projection = Proj(metric)
        report.update(layers=summaries, reporting_layer='ucrb_reporting', support_layer='ucrb_buffer_20km',
                      boundary_sha256=digest(gpkg), buffer_distance_m=20000,
                      buffer_crs=metric.to_wkt(), native_invalid_repaired=native_invalid,
                      projected_repairs=projected_repairs,
                      sampled_max_linear_scale=max(projection.get_factors(
                          lo + (hi-lo)*i/10, la + (ha-la)*j/10).tissot_semimajor
                          for i in range(11) for j in range(11)),
                      buffer_caveat='Local projected buffer, not exact geodesic; routing closure not guaranteed')
        mask = out / 'masks/ucrb_masks.nc'
        subprocess.run([sys.executable, str(root / 'bin/build_nwm_domain_masks.py'),
                        '--boundary', str(gpkg), '--layer', 'ucrb_buffer_20km',
                        '--output', str(mask)], check=True)
        report.update(masks=json.loads(mask.with_suffix('.json').read_text()), mask_sha256=digest(mask))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        _, masks, lat, lon = load_masks(mask)
        fig, axes = plt.subplots(1, 2, figsize=(13, 10))
        for ax in axes:
            gpd.read_file(gpkg, layer='ucrb_buffer_20km').plot(ax=ax, color='#dce9f1', edgecolor='#7d97a6')
            gpd.read_file(gpkg, layer='ucrb_reporting').plot(ax=ax, color='#c6dfd1', edgecolor='#15573f', linewidth=1)
            ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude'); ax.grid(alpha=.2)
        if 'wbdhu4' in layers:
            subregions.to_crs(4326).boundary.plot(ax=axes[0], color='#55816d', linewidth=.6)
        axes[0].set_title('UCRB: HU2 14 and 20-km buffer\nHU4 subregions shown for context')
        axes[1].contour(lon, lat, masks['forcing_mask'], levels=[.5], colors=['#dc8d18'], linewidths=1.1)
        axes[1].contour(lon, lat, masks['model_mask'], levels=[.5], colors=['#245cad'], linewidths=.5)
        axes[1].set_title('Aligned CONUS-grid masks\nForcing retains additional inactive land/water cells')
        fig.legend(handles=[Patch(color='#c6dfd1', label='HU2 14 reporting region'),
            Patch(color='#dce9f1', label='20-km support buffer'),
            Line2D([], [], color='#dc8d18', label='Forcing mask'),
            Line2D([], [], color='#245cad', label='Model mask')], loc='lower center', ncol=2)
        fig.tight_layout(rect=(0, .07, 1, 1))
        fig.savefig(out / 'boundaries/ucrb_domain.png', dpi=160, bbox_inches='tight')
        plt.close(fig)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(out / 'manifest.json', report)


if __name__ == '__main__':
    main()
