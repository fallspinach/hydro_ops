"""Conservative marginal cost of three extra Stage-IV revision days.

This simulates invalidation, not a measured change in NOAA precipitation values.
All baseline, forcing, receipts and caches are private to the benchmark.
"""
import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from benchmark_nrt_extension import clone, receipt

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/stage4-downstream-benchmark-{job}'
    campaign.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
    # Match the extra source days in acquisition benchmark 4699394.
    extra = [date(2026, 9, 20) + timedelta(days=i) for i in range(3)]
    start, end = extra[0] - timedelta(days=1), extra[-1] + timedelta(days=1)
    report = {'status': 'running', 'job_id': job, 'extra_baseline_days': list(map(str, extra)),
              'publication_range': [str(start), str(end)],
              'scope': 'conservative simulated invalidation; excludes downloads, summaries and CNRFC sync'}
    target = campaign / 'report.json'
    as_of = datetime.now(UTC)
    os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = str(campaign / 'cache')

    def run(label):
        tick = time.monotonic()
        result = cycle.run_cycle(root, scratch / label, start, end, as_of,
                                 baseline_root=campaign / 'baseline', output_root=campaign / 'nrt',
                                 state_root=campaign / 'status', pipeline='staged_v1')
        report[label] = {'seconds': time.monotonic() - tick, 'cycle': result}
        _atomic_json(target, report)
        if result['status'] != 'passed':
            raise ValueError(f'{label} failed: {result.get("errors")}')
        return result

    try:
        # Clone accepted production baselines; seed publication/cache separately,
        # so setup cost is not attributed to the lookback extension.
        for i in range((end - start).days + 3):
            day = start - timedelta(days=1) + timedelta(days=i)
            clone(cycle.day_path(root / 'forcing/outputs/conus/baseline/hourly', day),
                  cycle.day_path(campaign / 'baseline', day))
        run('setup')
        unchanged = run('no_change')
        if any(d['status'] != 'unchanged' for d in unchanged['days']):
            raise ValueError('No-change control was not unchanged; inputs may be moving')
        withheld = campaign / 'withheld'
        withheld.mkdir()
        for day in extra:
            path = cycle.day_path(campaign / 'baseline', day)
            path.rename(withheld / path.name)
        # Force dependent calendar publications, simulating changed input identity.
        for i in range((end - start).days + 1):
            path = receipt(cycle.day_path(campaign / 'nrt', start + timedelta(days=i)))
            record = cycle.read_json(path)
            record['input_fingerprint'] = 'benchmark-simulated-stage4-revision'
            _atomic_json(path, record)
        changed = run('three_extra_days')
        rebuilt = [d for d in changed['staged_timings']['baseline_days'] if d['status'] == 'published']
        if {d['day'] for d in rebuilt} != set(map(str, extra)):
            raise ValueError(f'Unexpected baseline rebuilds: {rebuilt}')
        repeat = run('repeat')
        if any(d['status'] != 'unchanged' for d in repeat['days']):
            raise ValueError('Repeat rebuilt outputs')
        report.update(status='passed', incremental_seconds=
                      report['three_extra_days']['seconds'] - report['no_change']['seconds'])
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(target, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
