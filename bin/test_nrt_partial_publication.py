"""Private replay of partial-day extension, mixed-source/GFS coverage and no-op."""
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import RecentNrt, day_path, identity
from hydro_ops.forcing.retro_publication import check_scratch


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/nrt-partial-publication-{job}'
    campaign.mkdir(exist_ok=False)
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}')
    work.mkdir(parents=True, exist_ok=True)
    check_scratch(work)
    engine = RecentNrt(root, work, datetime.now(UTC),
        baseline_root=campaign / 'baseline', output_root=campaign / 'nrt')
    day = date(2026, 9, 20)
    report = {'status': 'running', 'scope': 'private historical partial-day replay', 'stages': []}
    target = campaign / 'acceptance.json'
    try:
        path = day_path(engine.output, day)
        for end_hour in (12, 15, 15):
            before = identity(path)
            started = time.monotonic()
            result = engine.produce_day(day, end_hour=end_hour, latest=True)
            report['stages'].append({'end_hour': end_hour, 'seconds': time.monotonic()-started, **result})
            with Dataset(path) as ds:
                assert len(ds.dimensions['time']) == end_hour + 1
                assert ds.calendar_day_complete == 'false'
                assert ds.prism_reconciliation_accepted == 'false'
            if len(report['stages']) == 3:
                assert result['status'] == 'unchanged' and identity(path) == before
            else:
                assert result['status'] == 'published'
            _atomic_json(target, report)
        before = identity(path)
        try:
            engine.produce_day(day, end_hour=12, latest=True)
        except RuntimeError as error:
            assert 'truncate' in str(error)
        else:
            raise AssertionError('Truncation was not rejected')
        assert identity(path) == before
        report.update(status='passed', truncation_guard='passed', no_op='passed')
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(target, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
