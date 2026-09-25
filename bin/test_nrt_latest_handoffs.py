"""Private partial-to-complete and PRISM handoff using accepted replay files."""
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from benchmark_nrt_extension import clone, receipt
from netCDF4 import Dataset

from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import RecentNrt, day_path, identity, read_json
from hydro_ops.forcing.retro_publication import check_scratch


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/nrt-latest-handoffs-{job}'
    campaign.mkdir(exist_ok=False)
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}')
    work.mkdir(parents=True, exist_ok=True)
    check_scratch(work)
    day = date(2026, 9, 20)
    seed = root / 'forcing/work/nrt-partial-publication-4631499'
    assert read_json(seed / 'acceptance.json')['status'] == 'passed'
    originals = {}
    for folder in ('baseline', 'nrt'):
        source = day_path(seed / folder, day)
        originals[source] = identity(source)
        clone(source, day_path(campaign / folder, day))
    engine = RecentNrt(root, work, datetime.now(UTC),
        baseline_root=campaign / 'baseline', output_root=campaign / 'nrt')
    report = {'status': 'running', 'stages': [], 'scope': 'private full-day and PRISM arrival replay'}
    target = campaign / 'acceptance.json'
    path = day_path(engine.output, day)
    try:
        for name, kwargs in [('complete_unconstrained', {'latest': True}),
                             ('prism_arrival', {}), ('unchanged_repeat', {})]:
            before = identity(path)
            started = time.monotonic()
            result = engine.produce_day(day, **kwargs)
            record = read_json(receipt(path))
            assert record['complete_day'] is True
            assert record['latest_model_ready_hour'].endswith('T23:00:00+00:00')
            assert result['prism_constrained'] == (name != 'complete_unconstrained')
            with Dataset(path) as ds:
                assert len(ds.dimensions['time']) == 24
                assert ds.calendar_day_complete == 'true'
            if name == 'unchanged_repeat':
                assert result['status'] == 'unchanged' and identity(path) == before
            report['stages'].append({'stage': name, 'seconds': time.monotonic()-started, **result})
            _atomic_json(target, report)
        before = identity(path)
        try:
            engine.produce_day(day, latest=True)
        except RuntimeError as error:
            assert 'PRISM-constrained' in str(error)
        else:
            raise AssertionError('PRISM downgrade was not rejected')
        assert identity(path) == before
        assert all(identity(p) == old for p, old in originals.items())
        report.update(status='passed', prism_downgrade_guard='passed', seed_unchanged=True)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(target, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
