import json
import runpy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.baseline_publication import accepted_baseline


def test_source_aware_receipt_and_policy_required(tmp_path):
    path = tmp_path/'20260913.LDASIN_DOMAIN1'
    day = date(2026, 9, 13)
    with Dataset(path, 'w') as ds:
        ds.createDimension('time', 24)
        t = ds.createVariable('time', 'f8', ('time',))
        t.units = 'hours since 2026-09-13'
        t[:] = np.arange(24)
        ds.cnrfc_stage4_policy = 'applied'
        ds.forcing_domain_policy = 'nrt_hrrr_gfs_static_envelope_v1'
        ds.forcing_domain_content_audit = 'all_24_hours_all_8_fields_active_complete_outside_envelope_missing_gfs_v1'
        ds.nrt_production_policy = 'source_aware_recent_nrt_v1'
    receipt = path.with_name(path.name+'.nrt-receipt.json')
    stat = path.stat()
    record = {'status': 'passed', 'day': str(day), 'sha256': 'test', 'published_identity': {
        'path': str(path.resolve()), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}}
    receipt.write_text(json.dumps(record))
    manifest = {'verified': True, 'verification': 'source_aware_recent_nrt_v1',
                'nrt_receipt': str(receipt), 'source_files': ['native']*95}
    path.with_name(path.name+'.manifest.json').write_text(json.dumps(manifest))
    assert accepted_baseline(path, day)
    record['published_identity']['bytes'] += 1
    receipt.write_text(json.dumps(record))
    assert not accepted_baseline(path, day)
    record['published_identity']['bytes'] -= 1
    receipt.write_text(json.dumps(record))
    with Dataset(path, 'a') as ds:
        ds.delncattr('cnrfc_stage4_policy')
    assert not accepted_baseline(path, day)


def test_unaccepted_existing_archive_forces_full_rebuild(tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    path = tmp_path/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'old unaccepted archive')
    for k, v in {'SLURM_ARRAY_TASK_ID': '0', 'HYDRO_OPS_START_DAY': str(day),
                 'HYDRO_OPS_OUTPUT_ROOT': str(tmp_path), 'HYDRO_OPS_ARCHIVE_DAILY': '1',
                 'SLURM_JOB_USER': 'test', 'SLURM_JOB_ID': '123'}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv('HYDRO_OPS_FORCING_DAY_TASK_FILE', raising=False)
    monkeypatch.delenv('HYDRO_OPS_PYTHON', raising=False)
    monkeypatch.setattr('hydro_ops.forcing.retro_publication.check_scratch', lambda p: None)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr('subprocess.run', run)
    worker = runpy.run_path(str(Path(__file__).resolve().parents[1]/'slurm/produce_forcing_day.py'))
    assert worker['main']() == 1
    assert '--force' in commands[0]
    assert path.read_bytes() == b'old unaccepted archive'
