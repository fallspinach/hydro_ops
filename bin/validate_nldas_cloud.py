#!/usr/bin/env python3
"""Isolated CMR/cloud download and all-variable compatibility acceptance."""
import argparse
import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.config import load_settings
from hydro_ops.download.nldas2 import Nldas2Downloader
from hydro_ops.forcing.daily_archive import create_daily_archive, verified_daily_archive
from hydro_ops.forcing.inventory import inspect_forcing_file
from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.source_selection import select_hourly_source, source_paths


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def compare(cloud, reference, hour):
    differences = []
    with Dataset(cloud) as a, Dataset(reference) as b:
        assert set(a.variables) == set(b.variables), 'Variable set differs'
        for name in a.variables:
            x, y = a[name], b[name]
            assert x.dtype == y.dtype and x.dimensions == y.dimensions, name
            def record(v):
                key = [slice(None)] * v.ndim
                if 'time' in v.dimensions:
                    axis = v.dimensions.index('time')
                    key[axis] = 0 if v.shape[axis] == 1 else hour
                return v[tuple(key)]
            vx, vy = record(x), record(y)
            np.testing.assert_array_equal(np.ma.getmaskarray(vx), np.ma.getmaskarray(vy), err_msg=name)
            np.testing.assert_array_equal(np.ma.getdata(vx)[~np.ma.getmaskarray(vx)],
                                          np.ma.getdata(vy)[~np.ma.getmaskarray(vy)], err_msg=name)
            for attr in set(x.ncattrs()) | set(y.ncattrs()):
                equal = attr in x.ncattrs() and attr in y.ncattrs()
                if equal:
                    try:
                        np.testing.assert_equal(x.getncattr(attr), y.getncattr(attr))
                    except AssertionError:
                        equal = False
                if not equal:
                    differences.append(f'{name}/{attr}')
                    assert attr not in {'units', 'calendar', '_FillValue', 'scale_factor', 'add_offset'}, differences
        global_differences = []
        for attr in set(a.ncattrs()) | set(b.ncattrs()):
            try:
                np.testing.assert_equal(a.getncattr(attr), b.getncattr(attr))
            except (AttributeError, AssertionError):
                global_differences.append(attr)
    return differences, global_differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--days', nargs='+', type=date.fromisoformat, required=True)
    parser.add_argument('--sample-days', nargs='*', type=date.fromisoformat, default=[])
    args = parser.parse_args()
    original = load_settings()
    work = args.work.resolve()
    if work.exists():
        raise FileExistsError('Use a fresh isolated acceptance directory')
    work.mkdir(parents=True)
    settings = replace(original, nldas_data_dir=work / 'inputs', work_root=work / 'work',
                       nldas_cookies=work / 'earthdata.cookies', nldas_discovery='cmr')
    downloader = Nldas2Downloader(settings)
    report = {'status': 'running', 'created': datetime.now(UTC).isoformat(), 'days': [], 'files': []}
    receipt = work / 'acceptance.json'
    try:
        for day in args.days + args.sample_days:
            granules = downloader.discover(day)
            report['days'].append({'day': str(day), 'available_hours': len(granules),
                                   'urls': [g.url for g in granules]})
            selected = granules if day in args.days else [g for g in granules if '.1200.' in g.destination.name]
            assert selected, f'No selected hours for {day}'
            for granule in selected:
                downloader.download_one(granule)
                cloud = granule.destination
                inventory = inspect_forcing_file(cloud, 'nldas2')
                assert inventory.valid, inventory.issues
                valid = datetime.fromisoformat(inventory.valid_time).replace(tzinfo=UTC)
                reference = next(p for p in source_paths('nldas2', original.nldas_data_dir, valid) if p.exists())
                before = reference.stat()
                attrs, globals_changed = compare(cloud, reference, valid.hour)
                old_inventory = inspect_forcing_file(reference, 'nldas2')
                assert inventory.grid_fingerprint == old_inventory.grid_fingerprint
                with (open_normalized_forcing(cloud, 'nldas2', valid_time=valid) as x,
                      open_normalized_forcing(reference, 'nldas2', valid_time=valid) as y):
                    xr.testing.assert_equal(x.load(), y.load())
                chosen = select_hourly_source(valid, settings.nldas_data_dir, work / 'no-hrrr')
                assert chosen.product == 'nldas2' and chosen.path == cloud
                same_bytes = digest(cloud) == digest(reference) if reference.name == cloud.name else None
                after = reference.stat()
                assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
                report['files'].append({'file': cloud.name, 'reference': str(reference),
                    'byte_identical': same_bytes, 'all_variable_values_and_masks_equal': True,
                    'production_normalized_reader_equal': True,
                    'variable_attribute_differences': attrs, 'global_attribute_differences': globals_changed})
                print(f'PASS {cloud.name} byte_identical={same_bytes}', flush=True)
            before = selected[0].destination.stat()
            assert downloader.download_one(selected[0]) == 'skipped'
            after = selected[0].destination.stat()
            assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
            report['days'][-1]['repeat_download_skipped'] = True
            if day in args.days and len(selected) == 24:
                daily = settings.nldas_data_dir / f'{day:%Y}/NLDAS_FORA0125_H.A{day:%Y%m%d}.020.nc'
                create_daily_archive([g.destination for g in selected], daily, day, work_directory=work / 'work')
                assert verified_daily_archive(daily, day)
                for g in selected:
                    compare(g.destination, daily, int(g.destination.name.split('.')[2][:2]))
                report['days'][-1]['daily_archive_verified'] = True
                valid = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
                # Expose just the daily path in a separate isolated tree.
                archive_root = work / 'archive-only'
                link = archive_root / daily.relative_to(settings.nldas_data_dir)
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(daily)
                assert select_hourly_source(valid, archive_root, work / 'no-hrrr').path == link
                with (open_normalized_forcing(link, 'nldas2', valid_time=valid) as x,
                      open_normalized_forcing(selected[12].destination, 'nldas2', valid_time=valid) as y):
                    xr.testing.assert_equal(x.load(), y.load())
                report['days'][-1]['daily_normalized_reader_equal'] = True
        empty_day = datetime.now(UTC).date()
        report['unpublished_probe'] = {'day': str(empty_day), 'granules': len(
            downloader.discover(empty_day, allow_unpublished=True))}
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        receipt.write_text(json.dumps(report, indent=2) + '\n')
    print(receipt)


if __name__ == '__main__':
    main()
