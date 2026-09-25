"""Explicit 14-file NRT mask repair; no remapping or PRISM recomputation."""
import fcntl
import json
import os
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

import apply_static_forcing_mask as writer
from benchmark_static_mask_chunks import verify

from hydro_ops.forcing.retro_publication import check_scratch


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'forcing/outputs/conus/nrt'
    campaign = root / f'forcing/work/nrt-static-mask-14-{os.environ["SLURM_JOB_ID"]}'
    campaign.mkdir(exist_ok=False)
    work = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{os.environ["SLURM_JOB_ID"]}')
    work.mkdir(parents=True, exist_ok=True)
    check_scratch(work)
    mask = root / 'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    dates = ['20260412'] + [f'202609{i:02d}' for i in range(1, 14)]
    report = {'status': 'running', 'scope': dates, 'files': []}
    with ExitStack() as locks:
        for path in (root / 'forcing/status/nrt-gfs/cycle.lock', output / '.summary-backfill.lock'):
            handle = locks.enter_context(path.open('a'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for day in dates:
                source = output / 'hourly' / day[:4] / day[4:6] / (day+'.LDASIN_DOMAIN1')
                before = writer.identity(source)
                manifest = source.with_name(source.name+'.manifest.json')
                old_manifest = manifest.read_bytes()
                if source.with_name(source.name+'.nrt-receipt.json').exists():
                    raise ValueError(f'Unexpected modern receipt; review source before repair: {source}')
                with tempfile.TemporaryDirectory(dir=work, prefix='nrt-mask-') as temporary:
                    scratch = Path(temporary)
                    staged_root = scratch / 'candidate'
                    staged = staged_root / source.relative_to(output / 'hourly')
                    staged.parent.mkdir(parents=True)
                    shutil.copy2(source, staged)
                    shutil.copy2(manifest, staged.with_name(staged.name+'.manifest.json'))
                    if writer.file_hash(source) != writer.file_hash(staged):
                        raise ValueError('Staging checksum mismatch')
                    state = campaign / 'audit'
                    writer.publish(staged, mask, staged_root, state, scratch, staged_rebuild=True, fast=True)
                    # Full independent all-hours/fields comparison before permanent publication.
                    seconds = verify(source, mask, staged)
                    if writer.identity(source) != before or manifest.read_bytes() != old_manifest:
                        raise ValueError('Original source changed during repair')
                    result = writer.transfer_publication(staged, source, state)
                    report['files'].append({**result, 'full_verification_seconds': seconds})
                    writer.atomic_json(campaign / 'acceptance.json', report)
            report['status'] = 'passed'
        except Exception as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            writer.atomic_json(campaign / 'acceptance.json', report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
