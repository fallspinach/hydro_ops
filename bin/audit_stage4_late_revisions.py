"""Observe days 8–10 without altering operational Stage-IV inputs or forcing."""
import argparse
import fcntl
import json
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.config import load_settings
from hydro_ops.download.stage4 import Stage4Downloader
from hydro_ops.download.stage4_convert import Stage4Converter
from hydro_ops.download.stage4_revision_audit import compare_netcdf, sha256, signature
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.work import temporary_work_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument('--monitor', action='store_true',
                        help='capture day-seven references; compare ages 8–10 against them')
    parser.add_argument('--candidate-root', type=Path,
                        help='existing Stage-IV data root instead of downloading private observations')
    args = parser.parse_args()
    settings = load_settings()
    if args.monitor and (args.candidate_root or args.as_of != datetime.now(UTC).date()):
        parser.error('--monitor requires live observations for the current UTC day')
    monitor = settings.work_root / 'stage4-revision-monitor'
    if args.monitor:
        monitor.mkdir(parents=True, exist_ok=True)
        lock = (monitor / 'monitor.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    output = settings.project_root / 'forcing/status/stage4-revisions' / f'{stamp}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = settings.work_root / f'stage4-revisions-{stamp}'
    candidate = args.candidate_root or snapshot
    report = {'status': 'running', 'as_of': str(args.as_of), 'observed_at_utc': stamp,
              'reference_policy': 'day_seven_snapshot' if args.monitor else 'operational_copy',
              'job_id': os.environ.get('SLURM_JOB_ID'), 'records': [],
              'note': 'Pairs are not independent days; unchanged bytes need no decoded comparison.'}
    downloader = Stage4Downloader(replace(settings, stage4_data_dir=candidate))
    try:
        for age in ((7, 10, 9, 8) if args.monitor else (10, 9, 8)):
            day = args.as_of - timedelta(days=age)
            directory = Path('realtime') / day.strftime('%Y/%m/%d')
            if not args.candidate_root:
                for item in downloader.discover_realtime(day):
                    downloader.download_one(item, refresh=True)
            paths = sorted((candidate / directory).glob('*.grb2'))
            if not paths:
                raise ValueError(f'No candidate files for {day}')
            if args.monitor and age == 7:
                destination = monitor / 'references' / directory
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(destination.name + '.part-' + stamp)
                    shutil.copytree(candidate / directory, temporary)
                    _atomic_json(temporary / 'capture.json', {
                        'valid_day': str(day), 'captured_at_utc': datetime.now(UTC).isoformat(),
                        'files': {p.name: sha256(p) for p in paths}})
                    temporary.replace(destination)
                report['day_seven_capture'] = str(destination / 'capture.json')
                continue
            for new in paths:
                old = ((monitor / 'references') if args.monitor else settings.stage4_data_dir) / directory / new.name
                row = {'file': new.name, 'day': str(day), 'age_days': age,
                       'reference': str(old), 'candidate': str(new)}
                report['records'].append(row)
                if not old.is_file():
                    row['result'] = 'no_reference'
                    continue
                if args.monitor:
                    capture = json.loads((old.parent / 'capture.json').read_text())
                    row['reference_captured_at_utc'] = capture['captured_at_utc']
                    if sha256(old) != capture['files'][old.name]:
                        raise ValueError(f'Reference snapshot modified: {old}')
                before = signature(old), signature(new)
                row.update(reference_mtime_utc=datetime.fromtimestamp(old.stat().st_mtime, UTC).isoformat(),
                           candidate_mtime_utc=datetime.fromtimestamp(new.stat().st_mtime, UTC).isoformat(),
                           reference_sha256=sha256(old), candidate_sha256=sha256(new))
                if row['reference_sha256'] == row['candidate_sha256']:
                    row['result'] = 'identical_bytes'
                else:
                    with tempfile.TemporaryDirectory(dir=temporary_work_root(settings, 'stage4-audit')) as tmp:
                        outputs = []
                        for label, path in [('old', old), ('new', new)]:
                            converter = Stage4Converter(replace(settings, stage4_data_dir=Path(tmp) / label))
                            converter.convert_grib2(path, 'realtime', refresh=True)
                            outputs.append(converter.destination(path.name, 'realtime'))
                        row['decoded_changes'] = compare_netcdf(*outputs)
                    row['result'] = 'decoded_changed' if row['decoded_changes'] else 'encoding_only'
                if before != (signature(old), signature(new)):
                    raise ValueError(f'Input changed during comparison: {new.name}')
                _atomic_json(output, report)
        report['counts'] = {key: sum(r['result'] == key for r in report['records'])
                            for key in ('identical_bytes', 'encoding_only', 'decoded_changed', 'no_reference')}
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(output, report)
        print(json.dumps({'report': str(output), 'status': report['status'],
                          'counts': report.get('counts')}), flush=True)


if __name__ == '__main__':
    main()
