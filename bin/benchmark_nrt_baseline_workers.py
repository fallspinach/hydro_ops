"""Paired full-baseline benchmark; private outputs, unchanged science and audits."""
import argparse
import json
import os
import time
from datetime import date, datetime
from pathlib import Path

from benchmark_nrt_extension import compare, receipt

from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--repair-gfs', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.campaign.resolve().relative_to(root / 'forcing/work')
    args.campaign.mkdir(parents=True, exist_ok=False)
    original = cycle.read_json(receipt(args.source))
    if original.get('status') != 'passed' or original['published_identity'] != cycle.identity(args.source):
        raise ValueError('Reference baseline is not accepted')
    as_of = datetime.fromisoformat(original['as_of'])
    day = date.fromisoformat(original['day'])
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    report = {'status': 'running', 'job': os.environ['SLURM_JOB_ID'], 'day': str(day),
              'scope': 'existing source cache; full baseline builds; reference-first pair', 'trials': {}}
    output = args.campaign / 'acceptance.json'
    source_identity = cycle.identity(args.source)
    try:
        trials = [('reference', 4, 8), ('parallel', 4, 8)] if args.repair_gfs else [('reference', 1, 4), ('parallel', 4, 8)]
        report['repair_gfs_experiment'] = args.repair_gfs
        for name, remap, assembly in trials:
            os.environ['HYDRO_OPS_NRT_REPAIR_WORKERS'] = '4' if args.repair_gfs and name == 'parallel' else '1'
            os.environ['HYDRO_OPS_NRT_GFS_SPARSE_WRITES'] = '1' if args.repair_gfs and name == 'parallel' else '0'
            os.environ['HYDRO_OPS_NRT_PRECIP_WORKERS'] = str(remap)
            os.environ['HYDRO_OPS_NRT_ASSEMBLY_WORKERS'] = str(assembly)
            started = time.monotonic()
            engine = cycle.RecentNrt(root, scratch / name, as_of,
                baseline_root=args.campaign / name, output_root=args.campaign / (name + '-unused'))
            path, record = engine.baseline_day(day)
            report['trials'][name] = {'seconds': time.monotonic() - started, 'file': str(path),
                'workers': cycle.baseline_workers(engine.config), 'stages': record['baseline_stage_timings']}
            _atomic_json(output, report)
            if record['input_fingerprint'] != original['input_fingerprint']:
                raise ValueError('Source inputs changed since reference; not a controlled benchmark')
        started = time.monotonic()
        compare(Path(report['trials']['reference']['file']), Path(report['trials']['parallel']['file']))
        compare(args.source, Path(report['trials']['parallel']['file']))
        if cycle.identity(args.source) != source_identity:
            raise ValueError('Original baseline changed')
        report.update(status='passed', comparison_seconds=time.monotonic() - started)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(output, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
