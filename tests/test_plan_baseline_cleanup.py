import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

spec = importlib.util.spec_from_file_location(
    'cleanup_plan', Path(__file__).resolve().parents[1]/'bin/plan_baseline_cleanup.py')
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


def test_plan_is_read_only_and_rejects_stale_or_unstable_replacement(tmp_path):
    day = date(2019, 1, 6)
    relative = '2019/01/20190106.LDASIN_DOMAIN1'
    baseline = tmp_path/'forcing/outputs/conus/baseline/hourly'/relative
    retro = tmp_path/'forcing/outputs/conus/retro/hourly'/relative
    baseline.parent.mkdir(parents=True)
    retro.parent.mkdir(parents=True)
    baseline.write_bytes(b'baseline remains')
    baseline.with_name(baseline.name+'.manifest.json').write_text('{}')
    with Dataset(retro, 'w') as ds:
        ds.createDimension('time', 24)
        time = ds.createVariable('time', 'f8', ('time',))
        time.units = 'hours since 2019-01-06'
        time[:] = np.arange(24)
        ds.archive_granularity = 'utc_calendar_day'
        ds.prism_reconciliation_accepted = 'true'
        ds.prism_precipitation_revision = 'stable'
        ds.forcing_domain_policy = plan.POLICY
        ds.forcing_domain_content_audit = plan.AUDIT
        ds.forcing_static_mask_sha256 = 'mask'
    original = plan.identity(retro)
    audit = tmp_path/'audit.json'
    audit.write_text(json.dumps({'published_identity': original, 'status': 'published',
                                 'mask_sha256': 'mask', 'candidate_sha256': 'digest'}))
    envelope = {'published_identity': original, 'mask_sha256': 'mask',
                'file_sha256': 'digest', 'audit': str(audit)}
    manifest = retro.with_name(retro.name+'.manifest.json')
    manifest.write_text(json.dumps({'static_envelope': envelope}))
    result = plan.check((tmp_path, day, 'mask'))
    assert result['status'] == 'eligible'
    assert len(result['delete']) == 2
    assert baseline.read_bytes() == b'baseline remains'
    with Dataset(retro, 'a') as ds:
        ds.prism_precipitation_revision = 'early'
    assert plan.check((tmp_path, day, 'mask'))['status'] == 'blocked'
    with Dataset(retro, 'a') as ds:
        ds.prism_precipitation_revision = 'stable'
    envelope['published_identity']['bytes'] += 1
    manifest.write_text(json.dumps({'static_envelope': envelope}))
    assert plan.check((tmp_path, day, 'mask'))['status'] == 'blocked'
    assert baseline.exists()
