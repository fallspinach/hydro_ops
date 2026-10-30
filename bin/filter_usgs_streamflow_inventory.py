"""Derive immutable model-period station lists from an accepted USGS snapshot."""
import argparse
import json
from datetime import UTC, date, datetime, time
from pathlib import Path

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json
from hydro_ops.download.usgs_inventory import (
    DOMAINS,
    csv_bytes,
    filter_record_period,
    sha256,
    station_features,
)


def export(target, stations, series):
    write_json(target / 'stations.json', {'stations': stations})
    atomic_bytes(target / 'stations.csv', csv_bytes(stations))
    write_json(target / 'stations.geojson', station_features(stations))
    atomic_bytes(target / 'station_ids.txt',
                 ('\n'.join(s['monitoring_location_id'] for s in stations) + '\n').encode())
    write_json(target / 'time_series.json', {'time_series': series})
    atomic_bytes(target / 'time_series.csv', csv_bytes([s['provider_time_series'] for s in series]))


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Default: latest passed CONUS inventory')
    parser.add_argument('--since', type=date.fromisoformat, default=date(1979, 1, 1))
    parser.add_argument('--unknown-end', choices=('keep', 'separate'), default='separate',
                        help='Default: place stations without confirmed overlap in separate lists')
    args = parser.parse_args()
    if args.snapshot:
        source = args.snapshot.resolve()
    else:
        latest = json.loads((root / 'observations/status/usgs/streamflow-inventory/latest.json')
                            .read_text())
        if latest.get('status') != 'passed':
            raise ValueError('Latest inventory has not passed')
        source = root / latest['snapshot']
    acceptance = source / 'acceptance.json'
    parent = json.loads(acceptance.read_text())
    if parent.get('status') != 'passed':
        raise ValueError('Source inventory has not passed')
    for path, expected in parent['files_sha256'].items():
        if sha256(source / path) != expected:
            raise ValueError(f'Source export checksum mismatch: {path}')
    stations = json.loads((source / 'stations.json').read_text())['stations']
    series = json.loads((source / 'time_series.json').read_text())['time_series']
    cutoff = datetime.combine(args.since, time(), tzinfo=UTC)
    selected, selected_series, counts = filter_record_period(stations, series, cutoff)
    uncertain, uncertain_series = [], []
    if args.unknown_end == 'separate':
        confirmed_ids = {s['monitoring_location_id'] for s in selected_series
                         if s['provider_time_series'].get('end')}
        uncertain = [s for s in selected if s['monitoring_location_id'] not in confirmed_ids]
        uncertain_series = [s for s in selected_series
                            if s['monitoring_location_id'] not in confirmed_ids]
        selected = [s for s in selected if s['monitoring_location_id'] in confirmed_ids]
        selected_series = [s for s in selected_series
                           if s['monitoring_location_id'] in confirmed_ids]
    destination = root / 'observations/static/stations/usgs/streamflow_selections' / (
        source.name + '-since-' + args.since.strftime('%Y%m%d')
        + ('-confirmed' if args.unknown_end == 'separate' else ''))
    destination.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'source_snapshot': str(source),
              'source_acceptance_sha256': sha256(acceptance),
              'since_utc': cutoff.isoformat(),
              'criterion': 'Any daily-mean-discharge series end >= cutoff',
              'unknown_end_policy': args.unknown_end,
              'time_semantics': 'Provider UTC endpoints compared without changing native metadata',
              'observation_values_checked': False, 'source_modified': False}
    try:
        export(destination, selected, selected_series)
        if uncertain:
            export(destination / 'unknown_record_end', uncertain, uncertain_series)
        domain_counts = {}
        for domain in DOMAINS:
            source_ids = set((source / 'domains' / domain / 'station_ids.txt').read_text().splitlines())
            rows = [s for s in selected if s['monitoring_location_id'] in source_ids]
            ids = {s['monitoring_location_id'] for s in rows}
            domain_series = [s for s in selected_series if s['monitoring_location_id'] in ids]
            export(destination / 'domains' / domain, rows, domain_series)
            uncertain_rows = [s for s in uncertain if s['monitoring_location_id'] in source_ids]
            uncertain_ids = {s['monitoring_location_id'] for s in uncertain_rows}
            if uncertain_rows:
                export(destination / 'unknown_record_end/domains' / domain, uncertain_rows,
                       [s for s in uncertain_series if s['monitoring_location_id'] in uncertain_ids])
            domain_counts[domain] = {'before': parent['domain_counts'][domain]['stations'],
                                    'stations': len(rows), 'time_series': len(domain_series),
                                    'unknown_record_end_stations': len(uncertain_rows)}
        files = {str(p.relative_to(destination)): sha256(p)
                 for p in destination.rglob('*') if p.is_file()}
        report.update(status='passed', created_utc=datetime.now(UTC).isoformat(),
                      stations_before=len(stations), stations=len(selected),
                      time_series_before=len(series), time_series=len(selected_series),
                      domain_counts=domain_counts, files_sha256=files, **counts)
        write_json(destination / 'acceptance.json', report)
        print(json.dumps({k: v for k, v in report.items() if k != 'files_sha256'}, indent=2))
        print(f'Output: {destination}')
    except BaseException as error:
        report.update(status='failed', error=str(error))
        write_json(destination / 'failure.json', report)
        raise


if __name__ == '__main__':
    main()
