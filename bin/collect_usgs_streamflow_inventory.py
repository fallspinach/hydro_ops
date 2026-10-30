"""Build a versioned CONUS daily-mean-discharge inventory and reporting-domain lists."""
import argparse
import fcntl
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json
from hydro_ops.download.usgs_inventory import (
    BASE,
    DOMAINS,
    Client,
    assemble,
    csv_bytes,
    select_domain,
    sha256,
    station_features,
)


def main():
    import geopandas as gpd
    from shapely.ops import unary_union

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', type=Path, help='Canonical existing snapshot to resume')
    args = parser.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('Use slurm/collect_usgs_streamflow_inventory.sh')
    root = Path(__file__).resolve().parents[1]
    stamp = args.resume.name if args.resume else datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    output = root / 'observations/static/stations/usgs/streamflow' / stamp
    if args.resume and args.resume.resolve() != output:
        raise ValueError('Resume must identify a canonical USGS streamflow snapshot')
    output.mkdir(parents=True, exist_ok=bool(args.resume))
    with (output / '.collection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output / 'acceptance.json').exists():
            raise FileExistsError('Accepted snapshots are immutable; create a new snapshot')
        state = root / 'observations/status/usgs/streamflow-inventory' / (
            'job-' + os.environ['SLURM_JOB_ID'])
        state.mkdir(parents=True, exist_ok=False)
        report = {'status': 'running', 'job_id': os.environ['SLURM_JOB_ID'],
                  'snapshot': str(output.relative_to(root)), 'api_version': 'v1',
                  'started_utc': datetime.now(UTC).isoformat(),
                  'selection': 'USGS daily mean discharge, all history, 48 contiguous US states + DC',
                  'parameter_code': '00060', 'statistic_id': '00003',
                  'time_series_downloaded': False, 'nwm_reaches_matched': False,
                  'time_semantics': 'Native timezone/DST flags and UTC record endpoints retained; '
                                    'no inferred daily UTC bounds or IANA time-zone substitution'}
        write_json(state / 'status.json', report)
        client = Client(root / 'observations/inputs/usgs/streamflow_inventory' / stamp)
        try:
            fields = client.fetch(BASE + 'monitoring-locations/queryables?f=json',
                                  'monitoring-location-queryables')['properties']
            zones = client.pages('time-zone-codes', {})
            combined = client.pages('combined-metadata', {
                'parameter_code': '00060', 'statistic_id': '00003', 'data_type': 'Daily values'})
            daily = client.pages('time-series-metadata', {
                'parameter_code': '00060', 'statistic_id': '00003',
                'computation_period_identifier': 'Daily'})
            stations, series, excluded = assemble(combined, daily, fields, zones)
            write_json(output / 'stations.json', {'stations': stations})
            atomic_bytes(output / 'stations.csv', csv_bytes(stations))
            write_json(output / 'stations.geojson', station_features(stations))
            write_json(output / 'time_series.json', {'time_series': series})
            atomic_bytes(output / 'time_series.csv', csv_bytes([
                s['provider_time_series'] for s in series]))
            write_json(output / 'time_zone_codes.json', {'features': zones})
            domain_counts = {}
            boundary_sources = {}
            for name, (directory, stem, layer) in DOMAINS.items():
                boundary = root / 'nwm/static/domains' / directory / 'boundaries' / (
                    stem + '_boundaries.gpkg')
                before = sha256(boundary)
                frame = gpd.read_file(boundary, layer=layer)
                if frame.crs is None or frame.empty or frame.geometry.isna().any():
                    raise ValueError(f'Invalid reporting boundary: {name}')
                geometry = unary_union(frame.to_crs(4326).geometry)
                if geometry.is_empty or not geometry.is_valid:
                    raise ValueError(f'Invalid dissolved reporting boundary: {name}')
                selected = select_domain(stations, geometry)
                if not selected:
                    raise ValueError(f'Unexpected empty reporting-domain inventory: {name}')
                if before != sha256(boundary):
                    raise ValueError('Reporting boundary changed during extraction')
                target = output / 'domains' / name
                ids = {s['monitoring_location_id'] for s in selected}
                selected_series = [s['provider_time_series'] for s in series
                                   if s['monitoring_location_id'] in ids]
                atomic_bytes(target / 'stations.csv', csv_bytes(selected))
                write_json(target / 'stations.geojson', station_features(selected))
                atomic_bytes(target / 'station_ids.txt', ('\n'.join(sorted(ids)) + '\n').encode())
                atomic_bytes(target / 'time_series.csv', csv_bytes(selected_series))
                domain_counts[name] = {'stations': len(selected), 'time_series': len(selected_series)}
                boundary_sources[name] = {'path': str(boundary.relative_to(root)), 'layer': layer,
                                          'sha256': before, 'predicate': 'boundary covers station point',
                                          'buffer_used': False}
            # Reopen all exports, preserve checksums and publish acceptance only at the end.
            files = {str(p.relative_to(output)): sha256(p)
                     for p in output.rglob('*') if p.is_file() and p.name != '.collection.lock'}
            for path in output.rglob('*.json'):
                json.loads(path.read_text())
            report.update(status='passed', finished_utc=datetime.now(UTC).isoformat(),
                stations=len(stations), time_series=len(series),
                national_series_crosschecked=len(daily), exclusions=excluded,
                site_types=dict(Counter(s.get('site_type_code') for s in stations)),
                missing_coordinates=sum(s['longitude'] is None for s in stations),
                time_zone_issues=sum(s['time_zone_issue'] is not None for s in stations),
                daylight_savings_flags=dict(Counter(s.get('uses_daylight_savings') for s in stations)),
                domain_counts=domain_counts, boundaries=boundary_sources, files_sha256=files,
                sources=client.sources)
            write_json(output / 'acceptance.json', report)
            write_json(state / 'status.json', report)
            write_json(state.parent / 'latest.json', report)
        except BaseException as error:
            report.update(status='failed', error=str(error), sources=client.sources)
            write_json(state / 'status.json', report)
            raise


if __name__ == '__main__':
    main()
