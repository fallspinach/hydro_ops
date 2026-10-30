#!/usr/bin/env python3
"""Isolated A/B/B/A timing of the operational Stage-IV realtime downloader."""
from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from hydro_ops.config import load_settings
from hydro_ops.download.stage4 import Stage4Downloader


def main():
    settings = load_settings()
    job = os.environ['SLURM_JOB_ID']
    root = settings.work_root / f'stage4-lookback-benchmark-{job}'
    root.mkdir(parents=True, exist_ok=False)
    # Use the same permanent filesystem as operational inputs; intermediates
    # retain the normal node-local scratch behavior. Never touch source archives.
    settings = replace(settings, stage4_data_dir=root / 'inputs', stage4_download_jobs=4)
    today = datetime.now(UTC).date()
    report = {'status': 'running', 'job_id': job, 'as_of_day': today.isoformat(),
              'download_workers': 4, 'order': [7, 10, 10, 7], 'runs': [],
              'scope': 'realtime acquisition and conversion only; no forcing regeneration',
              'data_root': str(settings.stage4_data_dir)}

    def save():
        temporary = root / 'report.json.part'
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(root / 'report.json')

    save()
    try:
        for index, lookback in enumerate(report['order']):
            downloader = Stage4Downloader(settings)
            run = {'lookback_days': lookback, 'calendar_days': lookback + 1, 'days': []}
            report['runs'].append(run)
            started = time.monotonic()
            for offset in range(lookback, -1, -1):
                day = today - timedelta(days=offset)
                tick = time.monotonic()
                found, downloaded = downloader.download_day(day, 'realtime')
                result = {'day': day.isoformat(), 'files': found, 'downloaded': downloaded,
                          'seconds': time.monotonic() - tick}
                run['days'].append(result)
                print(json.dumps({'run': index + 1, 'lookback': lookback, **result}), flush=True)
                save()
            run['seconds'] = time.monotonic() - started
            save()
        averages = {str(n): statistics.mean(r['seconds'] for r in report['runs']
                                            if r['lookback_days'] == n) for n in (7, 10)}
        report.update(status='passed', mean_seconds=averages,
                      added_seconds=averages['10'] - averages['7'],
                      ratio=averages['10'] / averages['7'])
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
