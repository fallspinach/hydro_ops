"""Build isolated B120 union + 20-km masks, then audited CONUS parameter subsets."""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import write_json


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def geometry(root, out, source):
    import geopandas as gpd
    from build_cnrfc_hu18_domain import METRIC, dissolve
    from shapely import make_valid

    if source.suffix.lower() != '.shp':
        raise ValueError('Expected a shapefile')
    for suffix in ('.shp', '.shx', '.dbf', '.prj'):
        if not source.with_suffix(suffix).is_file():
            raise FileNotFoundError(source.with_suffix(suffix))
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'domain': 'b120', 'job_id': os.environ['SLURM_JOB_ID'],
              'definition': 'Union of all supplied basin polygons, buffered outward 20000 m',
              'model_smoke_tested': False, 'operational_cutover': False}
    try:
        originals = out / 'boundaries/sources'
        originals.mkdir(parents=True)
        copies = {}
        for path in sorted(source.parent.glob(source.stem + '.*')):
            if not path.is_file():
                continue
            before = digest(path)
            target = originals / path.name
            shutil.copy2(path, target)
            if digest(target) != before or digest(path) != before:
                raise ValueError('Source changed or copy mismatch')
            copies[path.name] = {'source': str(path), 'sha256': before}
        frame = gpd.read_file(originals / source.name)
        if frame.crs is None or frame.empty or frame.geometry.isna().any() or frame.is_empty.any():
            raise ValueError('Missing CRS or empty/null basin geometry')
        if not frame.geom_type.isin(['Polygon', 'MultiPolygon']).all():
            raise ValueError('Only polygon basins are supported')
        invalid = int((~frame.is_valid).sum())
        source_crs = str(frame.crs)
        frame.geometry = frame.geometry.map(make_valid)
        union, projected_repairs = dissolve(frame.to_crs(METRIC))
        buffered = union.buffer(20000, quad_segs=64)
        if not buffered.is_valid or buffered.is_empty or union.difference(buffered).area > 1:
            raise ValueError('Invalid buffer containment')
        gpkg = out / 'boundaries/b120_boundaries.gpkg'
        frame.to_crs(4326).to_file(gpkg, layer='repaired_basins', driver='GPKG')
        for name, shape in [('b120_union', union), ('b120_buffer_20km', buffered)]:
            gpd.GeoDataFrame({'name': [name]}, geometry=[shape], crs=METRIC).to_crs(4326).to_file(
                gpkg, layer=name, driver='GPKG')
        report.update(source_files=copies, source_crs=source_crs, basin_count=len(frame),
                      native_invalid_repaired=invalid, projected_repairs=projected_repairs,
                      buffer_crs=METRIC.to_wkt(), buffer_distance_m=20000,
                      reporting_layer='b120_union', support_layer='b120_buffer_20km',
                      boundary_sha256=digest(gpkg),
                      geometry_caveat='Local projected buffer, not exact geodesic; original invalid geometry retained in sources')
        mask = out / 'masks/b120_masks.nc'
        subprocess.run([sys.executable, str(root / 'bin/build_nwm_domain_masks.py'),
                        '--boundary', str(gpkg), '--layer', 'b120_buffer_20km',
                        '--output', str(mask)], check=True)
        report.update(masks=json.loads(mask.with_suffix('.json').read_text()), mask_sha256=digest(mask))
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        _, masks, lat, lon = load_masks(mask)
        fig, axes = plt.subplots(1, 2, figsize=(12, 9))
        for ax in axes:
            gpd.read_file(gpkg, layer='b120_buffer_20km').plot(ax=ax, color='#dde8ef', edgecolor='#71899c')
            gpd.read_file(gpkg, layer='b120_union').boundary.plot(ax=ax, color='#154f40', linewidth=.8)
            ax.set_xlabel('Longitude'); ax.set_ylabel('Latitude'); ax.grid(alpha=.2)
        frame.to_crs(4326).boundary.plot(ax=axes[0], color='#3f806e', linewidth=.5)
        axes[0].set_title('B120 basins, union and 20-km support buffer')
        axes[1].contour(lon, lat, masks['forcing_mask'], levels=[.5], colors=['#d28a1a'], linewidths=.6)
        axes[1].contour(lon, lat, masks['model_mask'], levels=[.5], colors=['#215caa'], linewidths=.4)
        axes[1].set_title('Forcing mask (orange); model mask (blue)')
        fig.tight_layout(); fig.savefig(out / 'boundaries/b120_domain.png', dpi=160); plt.close(fig)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(out / 'manifest.json', report)


def parameters(root, out, *, domain_name='b120', reporting_layer='b120_union'):
    report = json.loads((out / 'manifest.json').read_text())
    mask = out / f'masks/{domain_name}_masks.nc'
    if report['status'] != 'passed' or report['mask_sha256'] != digest(mask):
        raise ValueError('Masks did not pass or have changed')
    if report['boundary_sha256'] != digest(out / f'boundaries/{domain_name}_boundaries.gpkg'):
        raise ValueError('Boundary changed')
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/{domain_name}-parameters')
    target = out / 'parameters'
    status = {'status': 'running', 'domain': domain_name, 'job_id': job, 'model_smoke_tested': False}
    with (out / 'parameter-extraction.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.exists():
            raise FileExistsError(target)
        try:
            window, masks, lat, lon = load_masks(mask)
            subprocess.run([sys.executable, str(root / 'bin/subset_nwm_domain.py'),
                            '--domain-dir', str(root / 'nwm/static/operational/nwm.v3.1.6/domain'),
                            '--domain-masks', str(mask), '--output-dir', str(scratch), '--execute'], check=True)
            manifest = json.loads((scratch / 'subset_manifest.json').read_text())
            if manifest['network']['topology_validation'] != 'passed':
                raise ValueError('Routing topology validation failed')
            with Dataset(scratch / 'wrfinput_CONUS.nc') as ds:
                np.testing.assert_array_equal(ds['XLAND'][0] == 1, masks['model_mask'])
                np.testing.assert_allclose(ds['XLAT'][0], lat, rtol=0, atol=1e-5)
                np.testing.assert_allclose(ds['XLONG'][0], lon, rtol=0, atol=1e-5)
            with Dataset(scratch / 'Fulldom_CONUS_FullRouting.nc') as ds:
                if (len(ds.dimensions['y']), len(ds.dimensions['x'])) != window.refined(4).shape:
                    raise ValueError('Routing alignment mismatch')
            shutil.copy2(mask, scratch / 'domain_masks.nc')
            manifest.update(reporting_boundary=str(out / f'boundaries/{domain_name}_boundaries.gpkg'),
                            reporting_boundary_layer=reporting_layer,
                            routing_acceptance_policy='Structural consistency; drainage-completeness masks retained; not perfect closure')
            write_json(scratch / 'subset_manifest.json', manifest)
            stage = out / ('.parameters-job-' + job)
            shutil.copytree(scratch, stage)
            files = {}
            for path in scratch.iterdir():
                if path.is_file():
                    checksum = digest(path)
                    if checksum != digest(stage / path.name):
                        raise ValueError(f'Checksum mismatch: {path.name}')
                    files[path.name] = {'sha256': checksum, 'bytes': path.stat().st_size}
            stage.rename(target)
            status.update(status='passed', files=files, model_grid_shape=window.shape,
                          routing_grid_shape=window.refined(4).shape, network=manifest['network'])
        except BaseException as error:
            status.update(status='failed', error=str(error))
            raise
        finally:
            write_json(out / 'parameter_acceptance.json', status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('geometry', 'parameters'))
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / 'nwm/static/domains/b120'
    if args.stage == 'geometry':
        if args.source is None:
            parser.error('Geometry stage requires --source')
        geometry(root, out, args.source.resolve())
    else:
        parameters(root, out)


if __name__ == '__main__':
    main()
