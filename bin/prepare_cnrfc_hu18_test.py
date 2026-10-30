"""Extract approved candidate parameters and isolated retrospective/NRT forcing samples."""
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from subset_nwm_forcing import subset

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import write_json

DOMAIN = 'cnrfc_hu18_v1'
CASES = [('retro', '1979-01-02'), ('nrt', '2026-04-12')]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(path):
    stat = path.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def approved(root):
    from hydro_ops.cnrfc_domain import require_legacy_grid
    require_legacy_grid(root)
    domain = root / 'nwm/static/domains' / DOMAIN
    record = json.loads((domain/'approval.json').read_text())
    if (record['status'] != 'approved'
            or digest(domain/'masks/cnrfc_hu18_masks.nc') != record['mask_sha256']
            or digest(domain/'boundaries/cnrfc_hu18_boundaries.gpkg') != record['boundary_sha256']):
        raise ValueError('Missing approval or changed approved geometry/masks')
    return domain


def main():
    root = Path(__file__).resolve().parents[1]
    domain = approved(root)
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/hu18-prepare')
    scratch.mkdir(parents=True, exist_ok=False)
    status = root / 'nwm/status' / DOMAIN / 'preparation' / f'job-{job}'
    report = {'status': 'running', 'job_id': job, 'domain': DOMAIN, 'forcing_samples': []}
    try:
        with (domain/'parameter-extraction.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            target = domain/'parameters'
            if target.exists():
                raise FileExistsError(target)
            work = scratch/'parameters'
            mask = domain/'masks/cnrfc_hu18_masks.nc'
            window, masks, lat, lon = load_masks(mask)
            subprocess.run([sys.executable, str(root/'bin/subset_nwm_domain.py'),
                '--domain-dir', str(root/'nwm/static/operational/nwm.v3.1.6/domain'),
                '--domain-masks', str(mask), '--output-dir', str(work), '--execute'], check=True)
            manifest = json.loads((work/'subset_manifest.json').read_text())
            if manifest['network']['topology_validation'] != 'passed':
                raise ValueError('Topology validation failed')
            with Dataset(work/'wrfinput_CONUS.nc') as ds:
                np.testing.assert_array_equal(ds['XLAND'][0] == 1, masks['model_mask'])
                np.testing.assert_allclose(ds['XLAT'][0], lat, rtol=0, atol=1e-5)
                np.testing.assert_allclose(ds['XLONG'][0], lon, rtol=0, atol=1e-5)
            with Dataset(work/'Fulldom_CONUS_FullRouting.nc') as ds:
                if (len(ds.dimensions['y']), len(ds.dimensions['x'])) != window.refined(4).shape:
                    raise ValueError('Routing grid alignment failed')
            shutil.copy2(mask, work/'domain_masks.nc')
            manifest.update(reporting_boundary=str(domain/'boundaries/cnrfc_hu18_boundaries.gpkg'),
                reporting_boundary_layer='cnrfc_hu18_union',
                routing_acceptance_policy='Structural consistency required; boundary diagnostics retained; no perfect closure claim',
                extraction_status='passed_not_model_smoke_tested')
            write_json(work/'subset_manifest.json', manifest)
            stage = domain/f'.parameters-job-{job}'
            shutil.copytree(work, stage)
            files = {}
            for source in work.iterdir():
                if source.is_file():
                    checksum = digest(source)
                    if checksum != digest(stage/source.name):
                        raise ValueError(f'Parameter copy mismatch: {source.name}')
                    files[source.name] = {'sha256': checksum, 'bytes': source.stat().st_size}
            stage.rename(target)
            parameters = {'status': 'passed', 'files': files, 'domain': DOMAIN,
                          'job_id': job, 'model_grid_shape': window.shape,
                          'routing_grid_shape': window.refined(4).shape, 'network': manifest['network'],
                          'model_smoke_tested': False}
            write_json(domain/'parameter_acceptance.json', parameters)
            report['parameters'] = str(domain/'parameter_acceptance.json')
        for stream, first in CASES:
            for offset in range(4):
                day = datetime.fromisoformat(first)+timedelta(days=offset)
                relative = Path(stream)/'hourly'/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                source = root/'forcing/outputs/conus'/relative
                before = identity(source)
                destination = root/'forcing/outputs'/DOMAIN/'tests'/relative
                record = subset(source, scratch/'forcing'/relative, mask)
                if identity(source) != before:
                    raise ValueError('Parent forcing changed while subsetting')
                if destination.exists():
                    raise FileExistsError(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name+'.partial')
                shutil.copy2(scratch/'forcing'/relative, temporary)
                if digest(temporary) != digest(scratch/'forcing'/relative):
                    raise ValueError('Forcing publication copy mismatch')
                temporary.replace(destination)
                record.update(output=str(destination), source_identity=before,
                              output_identity=identity(destination))
                write_json(destination.with_name(destination.name+'.subset.json'), record)
                report['forcing_samples'].append(record)
                write_json(status/'acceptance.json', report)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(status/'acceptance.json', report)


if __name__ == '__main__':
    main()
