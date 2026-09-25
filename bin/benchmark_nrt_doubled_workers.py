"""Reference/doubled/reference worker benchmark, private mixed-source baselines."""
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from benchmark_nrt_extension import compare

from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import RecentNrt
from hydro_ops.forcing.retro_publication import check_scratch


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    campaign = root / f'forcing/work/nrt-doubled-workers-{job}'
    campaign.mkdir(exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}')
    scratch.mkdir(parents=True, exist_ok=True)
    check_scratch(scratch)
    report = {'status': 'running', 'trials': [], 'scope': '128 CPUs reserved; worker scaling, not node scaling'}
    as_of = datetime.now(UTC)
    fingerprints, paths = [], []
    try:
        for name, assembly, remap, repair in [('reference_before', 8, 4, 4),
                                              ('doubled', 16, 8, 8),
                                              ('reference_after', 8, 4, 4)]:
            os.environ.update(HYDRO_OPS_NRT_ASSEMBLY_WORKERS=str(assembly),
                              HYDRO_OPS_NRT_PRECIP_WORKERS=str(remap),
                              HYDRO_OPS_NRT_REPAIR_WORKERS=str(repair), HYDRO_OPS_NRT_GFS_SPARSE_WRITES='1')
            engine = RecentNrt(root, scratch / name, as_of,
                baseline_root=campaign / name, output_root=campaign / (name+'-unused'))
            started = time.monotonic()
            path, record = engine.baseline_day(date(2026, 9, 20))
            report['trials'].append({'name': name, 'seconds': time.monotonic()-started,
                                    'workers': [assembly, remap, repair], 'stages': record['baseline_stage_timings']})
            paths.append(path)
            fingerprints.append(record['input_fingerprint'])
            _atomic_json(campaign / 'acceptance.json', report)
        if len(set(fingerprints)) != 1:
            raise ValueError('Input changes invalidate comparison')
        compare(paths[0], paths[1])
        compare(paths[0], paths[2])
        reference = (report['trials'][0]['seconds'] + report['trials'][2]['seconds']) / 2
        doubled = report['trials'][1]['seconds']
        report.update(status='passed', comparison='all variables/masks/metadata passed',
                      speedup=reference/doubled, time_saved_percent=100*(1-doubled/reference))
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(campaign / 'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
