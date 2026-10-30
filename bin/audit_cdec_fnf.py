"""Rebuild normalized records from retained CDEC responses and verify every CSV."""
import argparse
import csv
import fcntl
import gzip
import hashlib
import io
import json
from datetime import date
from pathlib import Path

from hydro_ops.download.cdec_fnf import normalize, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-report', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = json.loads(args.run_report.read_text())
    if (report.get('status') != 'passed' or report.get('expected_series') != 47
            or report.get('completed') != 47 or report.get('failed')):
        raise ValueError('Full 47-series refresh must pass before audit')
    result = {'status': 'running', 'run_report': str(args.run_report), 'series': []}
    destination = args.run_report.with_name('audit.json')
    with (root / 'observations/status/cdec/fnf/update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        try:
            seen = set()
            for item in report['series']:
                key = (item['station_id'], item['resolution'])
                if key in seen or item['status'] != 'passed':
                    raise ValueError('Duplicate/failed series')
                seen.add(key)
                raw = gzip.decompress((root / item['raw_file']).read_bytes())
                if hashlib.sha256(raw).hexdigest() != item['source_sha256']:
                    raise ValueError('Retained raw response checksum mismatch')
                records = normalize(json.loads(raw), item, date.fromisoformat(item['requested_end']))
                directory = root / item['processed_directory']
                current = json.loads((directory / 'manifest.json').read_text())
                if current['source_sha256'] != item['source_sha256'] or current['status'] != 'passed':
                    raise ValueError('Series manifest changed since refresh')
                years = sorted({r['timestamp_pst'][:4] for r in records})
                if {p.stem for p in directory.glob('????.csv')} != set(years):
                    raise ValueError('Unexpected annual files')
                for year in years:
                    buffer = io.StringIO(newline='')
                    writer = csv.DictWriter(buffer, fieldnames=list(records[0]))
                    writer.writeheader()
                    writer.writerows(r for r in records if r['timestamp_pst'].startswith(year))
                    if (directory / (year + '.csv')).read_bytes() != buffer.getvalue().encode():
                        raise ValueError(f'CSV differs from retained source: {key}/{year}')
                result['series'].append({'station_id': key[0], 'resolution': key[1],
                                         'rows_verified': len(records), 'year_files': len(years)})
            if sum(s[1] == 'daily' for s in seen) != 23 or sum(s[1] == 'monthly' for s in seen) != 24:
                raise ValueError('Unexpected duration coverage')
            result['status'] = 'passed'
        except (ValueError, KeyError, OSError) as error:
            result.update(status='failed', error=str(error))
            raise
        finally:
            write_json(destination, result)


if __name__ == '__main__':
    main()
