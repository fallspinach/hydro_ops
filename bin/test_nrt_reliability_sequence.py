"""Real-data isolated worker sequence; scheduler wiring is a separate acceptance gate."""
import argparse
import fcntl
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from benchmark_nrt_extension import clone
from benchmark_nrt_revisions import snapshot

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.relative_to(root / 'forcing/work')
    campaign.mkdir(parents=True, exist_ok=False)
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    report = {'status': 'running', 'scope': 'worker sequence, lock exclusion, failure preservation, retry; not live cron'}
    options = {'output_root': campaign / 'nrt', 'baseline_root': campaign / 'baseline', 'state_root': campaign / 'status'}
    try:
        source = root / 'forcing/work/nrt-extension-20260919T021605/baseline'
        originals = snapshot(source)
        for day in range(13, 17):
            d = date(2026, 9, day)
            clone(cycle.day_path(source, d), cycle.day_path(options['baseline_root'], d))
        reports = []
        for name, start, end in [('six_hourly_1', 14, 14), ('six_hourly_2', 15, 15), ('daily_revisit', 14, 15)]:
            result = cycle.run_cycle(root, work / name, date(2026, 9, start), date(2026, 9, end),
                                     datetime.now(UTC), requested_at=datetime.now(UTC).isoformat(), **options)
            _atomic_json(campaign / f'{name}.json', result)
            if result['status'] != 'passed':
                raise RuntimeError(f'{name} failed')
            if name == 'daily_revisit' and any(d['status'] != 'unchanged' for d in result['days']):
                raise RuntimeError('Daily revisit unexpectedly rebuilt data')
            reports.append(result)
        before = {**snapshot(options['baseline_root']), **snapshot(options['output_root'])}
        with (options['state_root'] / 'cycle.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                cycle.run_cycle(root, work / 'locked', date(2026, 9, 15), date(2026, 9, 15), datetime.now(UTC), **options)
            except BlockingIOError:
                report['lock_exclusion'] = 'passed'
            else:
                raise RuntimeError('Concurrent writer was not excluded')
        original_engine = cycle.RecentNrt

        class FailedEngine(original_engine):
            def produce_day(self, day):
                raise RuntimeError('Injected worker outage before publication')

        cycle.RecentNrt = FailedEngine
        try:
            failed = cycle.run_cycle(root, work / 'failure', date(2026, 9, 15), date(2026, 9, 15), datetime.now(UTC), **options)
        finally:
            cycle.RecentNrt = original_engine
        if failed['status'] != 'failed' or not failed['errors']:
            raise RuntimeError('Worker failure not reported')
        retry = cycle.run_cycle(root, work / 'retry', date(2026, 9, 15), date(2026, 9, 15), datetime.now(UTC), **options)
        if retry['status'] != 'passed' or any(d['status'] != 'unchanged' for d in retry['days']):
            raise RuntimeError('Retry failed to converge')
        if any(cycle.identity(Path(p)) != old for p, old in {**before, **originals}.items()):
            raise RuntimeError('Failure/retry changed accepted files or seed originals')
        report.update(status='passed', cycles=reports, failure_preservation='passed', retry=retry)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(campaign / 'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
