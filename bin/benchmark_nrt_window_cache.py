"""Paired fresh-scratch reconciliation benchmark with one cross-cycle window hit."""
import argparse
import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from benchmark_nrt_extension import clone, compare, receipt

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--campaign', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.campaign.resolve().relative_to(root / 'forcing/work')
    args.campaign.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'], 'trials': {}}
    result = args.campaign / 'acceptance.json'
    target = date(2026, 9, 15)
    as_of = datetime.fromisoformat(cycle.read_json(args.source / 'acceptance.json')['first_cycle']['as_of'])
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    try:
        for offset in (-2, -1, 0, 1):
            day = target + timedelta(days=offset)
            clone(cycle.day_path(args.source / 'baseline', day), cycle.day_path(args.campaign / 'baseline', day))
        snapshot = {str(p): cycle.identity(p) for p in (args.campaign / 'baseline').glob('*/*/*.LDASIN_DOMAIN1')}
        # Seeds day 14 windows (14 and 15). Day 15 needs windows 15 and 16:
        # one should hit and one should be newly computed in the optimized arm.
        for name, day, cache in [('seed', target - timedelta(days=1), True),
                                  ('reference', target, False), ('cached', target, True)]:
            if cache:
                os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = str(args.campaign / 'window-cache')
            else:
                os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = ''
            started = time.monotonic()
            trial = cycle.run_cycle(root, scratch / name, day, day, as_of,
                baseline_root=args.campaign / 'baseline', output_root=args.campaign / name,
                state_root=args.campaign / (name + '-status'), requested_at=datetime.now(UTC).isoformat())
            report['trials'][name] = {'seconds': time.monotonic() - started, 'cycle': trial}
            _atomic_json(result, report)
            if trial['status'] != 'passed':
                raise ValueError(f'{name} failed')
            if any(cycle.identity(Path(p)) != old for p, old in snapshot.items()):
                raise ValueError('Baseline changed; not reconciliation-only benchmark')
            final = cycle.day_path(args.campaign / name, day)
            report['trials'][name]['stages'] = cycle.read_json(receipt(final))['stage_timings']
        hits = [s for s in report['trials']['cached']['stages'] if s['stage'] == 'persistent_window_hit']
        if len(hits) != 1:
            raise ValueError(f'Expected one reused window, got {len(hits)}')
        started = time.monotonic()
        compare(cycle.day_path(args.campaign / 'reference', target), cycle.day_path(args.campaign / 'cached', target))
        report.update(status='passed', comparison_seconds=time.monotonic() - started,
                      finished=datetime.now(UTC).isoformat())
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(result, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
