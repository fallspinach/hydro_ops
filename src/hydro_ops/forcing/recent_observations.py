"""Best-effort new-hour precipitation acquisition for latest-hour publication.

Run sources in separate processes: NetCDF conversion is not thread safe. A
source outage must not block model-ready HRRR/GFS fallback publication.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import requests

from hydro_ops.config import load_settings
from hydro_ops.download.mrms import MrmsDownloader
from hydro_ops.download.stage4 import Stage4Downloader
from hydro_ops.download.stage4_convert import Stage4Converter, is_netcdf


def acquire(source, day, end_hour):
    settings = load_settings()
    report = {'source': source, 'downloaded': 0, 'skipped': 0, 'unavailable': 0, 'errors': []}
    if source == 'mrms':
        downloader = MrmsDownloader(settings)
        items = [downloader.file(product, datetime.combine(day, datetime.min.time(), UTC)
                                 + timedelta(hours=hour))
                 for hour in range(end_hour + 1) for product in settings.mrms_products]
        def fetch(item):
            if is_netcdf(item.netcdf):
                return 'skipped'
            return downloader.download_one(item)
    else:
        downloader = Stage4Downloader(settings)
        converter = Stage4Converter(settings)
        items = downloader.discover_realtime(day)
        # Include available six-hour totals needed by the CNRFC policy, but
        # exclude unrelated daily products and future hourly records.
        items = [item for item in items if
                 (item.destination.name.endswith('.01h.grb2') and
                  int(item.destination.name.split('.')[1][-2:]) <= end_hour)
                 or item.destination.name.endswith('.06h.grb2')]
        def fetch(item):
            if is_netcdf(converter.destination(item.destination.name, 'realtime')):
                return 'skipped'
            downloader.download_one(item)
            converter.convert_grib2(item.destination, 'realtime')
            return 'downloaded'
    for item in items:
        try:
            result = fetch(item)
            report['skipped' if result == 'skipped' else 'downloaded'] += 1
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                report['unavailable'] += 1
            else:
                report['errors'].append(str(error))
        except (requests.RequestException, OSError, RuntimeError, subprocess.SubprocessError) as error:
            report['errors'].append(str(error))
    report['status'] = 'degraded' if report['errors'] else 'passed'
    return report


def refresh_recent_observations(project, day, end_hour, timeout=600):
    """Bound both source refreshes to ten minutes total, with durable logs."""
    log_root = project / 'forcing/logs/recent-observations'
    log_root.mkdir(parents=True, exist_ok=True)

    def run(source):
        log = log_root / f'{os.environ.get("SLURM_JOB_ID", "manual")}-{day}-{source}.log'
        started = time.monotonic()
        with log.open('w') as stream:
            process = subprocess.Popen(
                [sys.executable, '-m', __name__, '--source', source,
                 '--day', day.isoformat(), '--end-hour', str(end_hour)],
                cwd=project, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=timeout)
                status = 'passed' if returncode == 0 else 'degraded'
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                returncode, status = None, 'timeout'
        return {'source': source, 'status': status, 'returncode': returncode,
                'seconds': round(time.monotonic()-started, 3), 'log': str(log)}

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(run, ('mrms', 'stage4')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('mrms', 'stage4'), required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--end-hour', type=int, choices=range(24), required=True)
    args = parser.parse_args()
    report = acquire(args.source, args.day, args.end_hour)
    print(json.dumps(report), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
