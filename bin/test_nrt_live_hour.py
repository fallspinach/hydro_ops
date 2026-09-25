"""Download one latest available HRRR hour and GFS bundle into private storage.

This is an acquisition test, not proof of continuous model-ready coverage.
"""
import argparse
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from hydro_ops.config import load_settings
from hydro_ops.download.gfs import GfsDownloader
from hydro_ops.download.hrrr import (
    ANALYSIS_RECORDS,
    PRECIPITATION_RECORD,
    HrrrDownloader,
    select_record,
)
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.source_selection import select_hourly_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Test private complete-prefix acquisition and publication')
    args = parser.parse_args()
    settings = load_settings()
    campaign = settings.project_root / f'forcing/work/nrt-live-hour-{os.environ["SLURM_JOB_ID"]}'
    campaign.mkdir(exist_ok=False)
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
    work.mkdir(parents=True, exist_ok=True)
    settings = replace(settings, hrrr_data_dir=campaign / 'hrrr', work_root=work)
    downloader = HrrrDownloader(settings)
    as_of = datetime.now(UTC)
    report = {'status': 'running', 'as_of': as_of.isoformat(), 'probes': [],
              'scope': 'one live source hour; not model-ready publication'}
    try:
        valid = None
        with downloader._session() as session:
            for offset in range(7):
                candidate = as_of.replace(minute=0, second=0, microsecond=0)-timedelta(hours=offset)
                try:
                    analysis = downloader._index(session, downloader.source_url(candidate, 0))
                    for selector in ANALYSIS_RECORDS:
                        select_record(analysis, selector)
                    precip = downloader._index(session, downloader.source_url(candidate-timedelta(hours=1), 1))
                    select_record(precip, PRECIPITATION_RECORD)
                except requests.HTTPError as error:
                    if error.response.status_code != 404:
                        raise
                    report['probes'].append({'hour': candidate.isoformat(), 'status': 'not_yet_published'})
                    continue
                valid = candidate
                break
        if valid is None:
            raise FileNotFoundError('No complete HRRR analysis/precipitation pair in latest seven hours')
        downloader.download_hour(valid)
        selected = select_hourly_source(valid, campaign / 'no-nldas', settings.hrrr_data_dir)
        assert selected.product == 'hrrr'
        bundle = GfsDownloader(campaign / 'gfs', work).hour(valid, as_of=as_of)
        report.update(status='passed', valid_time=valid.isoformat(), hrrr_file=str(selected.path),
                      gfs={k: bundle.attrs.get(k) for k in ('valid_time', 'cycle', 'lead', 'url')})
        if args.publish:
            from hydro_ops.forcing.nrt_cycle import RecentNrt, day_path, identity
            report.update(status='running', scope='live HRRR/GFS prefix refresh to private latest-hour publication')
            _atomic_json(campaign / 'acceptance.json', report)
            started = time.monotonic()
            first = valid.replace(hour=0)-timedelta(hours=5)
            for index in range(valid.hour + 6):
                downloader.download_hour(first + timedelta(hours=index))
            report['prefix_acquisition_seconds'] = time.monotonic()-started
            engine = RecentNrt(settings.project_root, work / 'production', as_of,
                baseline_root=campaign / 'baseline', output_root=campaign / 'nrt')
            # Retain any already available preferred local products. HRRR is
            # independently refreshed, including the precipitation halo.
            engine.layout = replace(engine.layout, hrrr_root=settings.hrrr_data_dir)
            engine.repair.layout = engine.layout
            engine.cache = campaign / 'gfs'
            started = time.monotonic()
            report['publication'] = engine.produce_day(valid.date(), end_hour=valid.hour, latest=True)
            report['publication_seconds'] = time.monotonic()-started
            path = day_path(engine.output, valid.date())
            before = identity(path)
            started = time.monotonic()
            report['repeat'] = engine.produce_day(valid.date(), end_hour=valid.hour, latest=True)
            report['repeat_seconds'] = time.monotonic()-started
            if report['repeat']['status'] != 'unchanged' or identity(path) != before:
                raise ValueError('Unchanged live repeat altered publication')
            report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(campaign / 'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
