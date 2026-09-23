"""Shared completeness gate for legacy and source-aware baseline publications."""
import json
from datetime import date

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.daily_archive import verified_daily_archive


def accepted_baseline(path, day):
    path = path.resolve()
    try:
        with Dataset(path) as data:
            time = data['time']
            stamps = num2date(time[:], time.units, calendar=getattr(time, 'calendar', 'standard'))
            if [t.strftime('%Y%m%d%H%M%S') for t in stamps] != [
                day.strftime('%Y%m%d')+f'{h:02d}0000' for h in range(24)
            ]:
                return False
            if day >= date(2020, 7, 1) and not getattr(data, 'cnrfc_stage4_policy', ''):
                return False
            policy = getattr(data, 'forcing_domain_policy', '')
            audit = getattr(data, 'forcing_domain_content_audit', '')
            if policy == 'nldas2_active_gaps_preserve_inactive_v3':
                return (audit == 'all_records_active_complete_outside_masked_inactive_preserved_v3'
                        and verified_daily_archive(path, day))
            if (policy != 'nrt_hrrr_gfs_static_envelope_v1'
                    or audit != 'all_24_hours_all_8_fields_active_complete_outside_envelope_missing_gfs_v1'
                    or getattr(data, 'nrt_production_policy', '') != 'source_aware_recent_nrt_v1'):
                return False
        receipt_path = path.with_name(path.name+'.nrt-receipt.json')
        receipt = json.loads(receipt_path.read_text())
        manifest = json.loads(path.with_name(path.name+'.manifest.json').read_text())
        stat = path.stat()
        return (receipt.get('status') == 'passed' and receipt.get('day') == str(day)
                and bool(receipt.get('sha256'))
                and receipt.get('published_identity') == {
                    'path': str(path.resolve()), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
                and manifest.get('verified') is True
                and manifest.get('verification') == 'source_aware_recent_nrt_v1'
                and manifest.get('nrt_receipt') == str(receipt_path))
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError):
        return False
