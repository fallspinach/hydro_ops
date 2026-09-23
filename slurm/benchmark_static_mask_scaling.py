"""Eight-task, two-writer scaling pilot; isolated outputs, separate full audits."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import socket
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'bin'))
from benchmark_static_mask_chunks import verify
from benchmark_static_mask_parallel import run_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['build', 'audit', 'summarize'])
    args = parser.parse_args()
    campaign = os.environ.get('MASK_SCALING_CAMPAIGN', os.environ.get('SLURM_ARRAY_JOB_ID', os.environ['SLURM_JOB_ID']))
    base = PROJECT/'forcing/work/static-mask-scaling'/f'job_{campaign}'
    if args.phase == 'summarize':
        reports = [json.loads((base/f'task-{i}'/'build.json').read_text()) for i in range(8)]
        audits = [json.loads((base/f'task-{i}'/'audit.json').read_text()) for i in range(8)]
        elapsed = max(r['finished_epoch'] for r in reports)-min(r['started_epoch'] for r in reports)
        overlap = min(r['finished_epoch'] for r in reports)-max(r['started_epoch'] for r in reports)
        summary = {'status': 'passed' if all(a['status'] == 'passed' for a in audits) else 'failed',
                   'files': sum(len(r['files']) for r in reports), 'build_wall_seconds': elapsed,
                   'files_per_hour': sum(len(r['files']) for r in reports)*3600/elapsed,
                   'all_eight_tasks_overlap_seconds': max(0, overlap),
                   'tasks': reports, 'audits': audits,
                   'scope': 'isolated benchmark; production unchanged; full audits outside timed build phase'}
        (base/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps({k: v for k, v in summary.items() if k not in ('tasks', 'audits')}), flush=True)
        return
    rank = int(os.environ['SLURM_ARRAY_TASK_ID'])
    folder = base/f'task-{rank}'
    sources = [PROJECT/f'forcing/outputs/conus/retro/hourly/2000/{rank+1:02}/2000{rank+1:02}{d:02}.LDASIN_DOMAIN1'
               for d in range(1, 9)]
    mask = PROJECT/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    if args.phase == 'build':
        folder.mkdir(parents=True, exist_ok=False)
        scratch = Path('/scratch')/os.environ['SLURM_JOB_USER']/f"job_{os.environ['SLURM_JOB_ID']}"/f'scaling-{rank}'
        scratch.mkdir(parents=True, exist_ok=True)
        report = {'rank': rank, 'node': socket.gethostname(), 'workers': 2,
                  'started_epoch': time.time(), 'started_utc': datetime.now(UTC).isoformat()}
        tasks = [(p, mask, scratch, folder/p.name, 'chunks', '') for p in sources]
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
            report['files'] = list(pool.map(run_file, tasks))
        report.update(finished_epoch=time.time(), status='passed')
        (folder/'build.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'rank': rank, 'phase': 'build', 'seconds': report['finished_epoch']-report['started_epoch']}), flush=True)
    else:
        audits = []
        for source in sources:
            seconds = verify(source, mask, folder/source.name)
            audits.append({'source': str(source), 'seconds': seconds})
        (folder/'audit.json').write_text(json.dumps({'status': 'passed', 'files': audits}, indent=2)+'\n')
        print(json.dumps({'rank': rank, 'phase': 'audit', 'status': 'passed'}), flush=True)


if __name__ == '__main__':
    main()
