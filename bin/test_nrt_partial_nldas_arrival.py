"""Private partial-day HRRR/GFS-to-NLDAS replacement replay."""
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing import complete_day, nrt_cycle
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.source_selection import select_hourly_source


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/nrt-partial-nldas-arrival-{job}'
    campaign.mkdir(exist_ok=False)
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}')
    engine = nrt_cycle.RecentNrt(root, work, datetime.now(UTC),
        baseline_root=campaign / 'baseline', output_root=campaign / 'nrt')
    day = date(2026, 9, 20)
    report = {'status': 'running', 'scope': 'controlled primary availability; private outputs', 'stages': []}
    def hrrr_only(t, nldas, hrrr):
        return select_hourly_source(t, nldas, hrrr, preference=('hrrr',))
    try:
        for label, selector in [('before_arrival', hrrr_only), ('after_arrival', select_hourly_source),
                                ('unchanged_repeat', select_hourly_source)]:
            nrt_cycle.select_hourly_source = complete_day.select_hourly_source = selector
            started = time.monotonic()
            result = engine.produce_day(day, end_hour=12, latest=True)
            assert result['gfs_hours'] == (13 if label == 'before_arrival' else 0)
            assert result['status'] == ('unchanged' if label == 'unchanged_repeat' else 'published')
            report['stages'].append({'stage': label, 'seconds': time.monotonic()-started, **result})
            _atomic_json(campaign / 'acceptance.json', report)
        path = nrt_cycle.day_path(engine.output, day)
        before = nrt_cycle.identity(path)
        nrt_cycle.select_hourly_source = complete_day.select_hourly_source = hrrr_only
        try:
            engine.produce_day(day, end_hour=12, latest=True)
        except RuntimeError as error:
            assert 'downgrade' in str(error)
        else:
            raise AssertionError('NLDAS downgrade was not rejected')
        assert nrt_cycle.identity(path) == before
        report.update(status='passed', downgrade_guard='passed')
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        nrt_cycle.select_hourly_source = complete_day.select_hourly_source = select_hourly_source
        _atomic_json(campaign / 'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
