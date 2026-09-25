"""Locked operational extension through the latest available contiguous hour."""
import fcntl
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from netCDF4 import Dataset, num2date

from hydro_ops.config import load_settings
from hydro_ops.download.hrrr import HrrrDownloader
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import RecentNrt, identity, read_json
from hydro_ops.forcing.retro_publication import check_scratch


def main():
    settings = load_settings()
    root = settings.project_root
    state = root / 'forcing/status/nrt-gfs'
    state.mkdir(parents=True, exist_ok=True)
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
    work.mkdir(parents=True, exist_ok=True)
    check_scratch(work)
    report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'], 'days': [],
              'started_utc': datetime.now(UTC).isoformat(), 'scope': 'latest contiguous extension'}
    with (state / 'cycle.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            now = datetime.now(UTC)
            engine = RecentNrt(root, work, now)
            paths = sorted(engine.output.glob('*/*/*.LDASIN_DOMAIN1'))
            if not paths:
                raise ValueError('Bootstrap requires an explicit reviewed starting date')
            last = paths[-1]
            receipt = read_json(last.with_name(last.name+'.nrt-receipt.json'))
            if receipt.get('status') != 'passed' or receipt.get('published_identity') != identity(last):
                raise ValueError('Latest existing NRT file lacks a current acceptance receipt')
            with Dataset(last) as data:
                t = data['time']
                times = num2date(t[:], t.units, only_use_cftime_datetimes=False)
                day = date.fromisoformat(receipt['day'])
                if not 1 <= len(times) <= 24 or [(v.date(), v.hour, v.minute, v.second) for v in times] != [(day, h, 0, 0) for h in range(len(times))]:
                    raise ValueError('Latest existing file is not a contiguous midnight prefix')
                accepted = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=len(times)-1)
                if len(times) == 24:
                    day += timedelta(days=1)
            report['latest_model_ready_hour'] = accepted.isoformat()
            downloader = HrrrDownloader(settings)
            # Bounded catch-up: fail visibly instead of silently skipping missing days.
            if (now.date()-day).days > 7:
                raise ValueError('More than seven catch-up days; submit an explicit backfill first')
            while day <= now.date():
                midnight = datetime.combine(day, datetime.min.time(), UTC)
                end_hour = -1
                for hour in range(-5, min(23, now.hour if day == now.date() else 23)+1):
                    valid = midnight + timedelta(hours=hour)
                    # Canonical acquisition is serialized ahead of the revision cycle.
                    try:
                        downloader.download_hour(valid)
                    except requests.HTTPError as error:
                        if error.response.status_code != 404:
                            raise
                        if valid <= accepted:
                            raise ValueError('Required historical HRRR input unavailable') from error
                        report['awaiting_source_hour'] = valid.isoformat()
                        break
                    if hour >= 0:
                        end_hour = hour
                if end_hour < 0 or midnight+timedelta(hours=end_hour) <= accepted:
                    break
                result = engine.produce_day(day, end_hour=end_hour, latest=True)
                accepted = midnight+timedelta(hours=end_hour)
                report['days'].append(result)
                report['latest_model_ready_hour'] = accepted.isoformat()
                _atomic_json(state / 'latest-extension.json', report)
                if end_hour < 23:
                    break
                day += timedelta(days=1)
            report['status'] = 'passed'
        except Exception as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            report['finished_utc'] = datetime.now(UTC).isoformat()
            _atomic_json(state / 'latest-extension.json', report)
            _atomic_json(state / f'extension-{os.environ["SLURM_JOB_ID"]}.json', report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
