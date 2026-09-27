"""Preserve job 4524572's current scratch segment while its controller is paused.

Read-only on model files. Once the model finishes, save all regular scratch files
and verify checksums before cancelling the paused allocation (never resume its
unsafe publisher). Failed copies or timeout do not cancel the allocation.
"""
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from netCDF4 import Dataset


def fingerprint(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    source = Path('/scratch/mpan/job_4524572/production_1979_v1-production-198706')
    target = root / 'nwm/recovery/chrtout_record_dimension/CONUS_4524572_198706'
    target.mkdir(parents=True, exist_ok=True)
    logs = root / 'nwm/runs/conus/retro/production_1979_v1/production/198706'
    report = {'status': 'copying', 'source': str(source), 'copied': {}, 'job': '4524572'}
    deadline = time.monotonic() + 12 * 3600

    def save():
        partial = target / 'recovery.json.part'
        partial.write_text(json.dumps(report, indent=2) + '\n')
        partial.replace(target / 'recovery.json')

    def copy(path):
        before = fingerprint(path)
        if report['copied'].get(path.name, {}).get('source_identity') == before:
            return
        destination = target / path.name
        partial = destination.with_name(destination.name + '.part')
        shutil.copy2(path, partial)
        checksum = sha(partial)
        if fingerprint(path) != before or sha(path) != checksum:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f'Source changed during copy: {path}')
        partial.replace(destination)
        report['copied'][path.name] = {'source_identity': before, 'sha256': checksum}

    try:
        while time.monotonic() < deadline:
            paths = sorted(source.glob('*.CHRTOUT_DOMAIN1'))
            # The model may still be writing the last file; defer it until completion.
            for path in paths[:-1]:
                if not path.is_file():
                    continue
                with Dataset(path) as data:
                    if 'streamflow' not in data.variables:
                        raise RuntimeError(f'Invalid hourly output: {path}')
                copy(path)
            report['raw_hourly_files'] = sum(name.endswith('.CHRTOUT_DOMAIN1') for name in report['copied'])
            save()
            model_log = logs / 'model.log'
            if model_log.exists() and 'The model finished successfully' in model_log.read_text():
                # The paused controller cannot remove or mutate completed outputs.
                for path in sorted(source.iterdir()):
                    if path.is_file() and not path.is_symlink():
                        copy(path)
                copy(model_log)
                report.update(status='preserved_model_complete',
                              raw_hourly_files=sum(name.endswith('.CHRTOUT_DOMAIN1') for name in report['copied']))
                save()
                subprocess.run(['scancel', '4524572'], check=True)
                report['allocation_cancelled_after_preservation'] = True
                save()
                return
            time.sleep(30)
        raise TimeoutError('Preservation timed out; allocation not cancelled')
    except BaseException as error:
        report.update(status='failed', error=str(error))
        save()
        raise


if __name__ == '__main__':
    main()
