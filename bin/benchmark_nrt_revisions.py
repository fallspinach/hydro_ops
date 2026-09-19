"""Isolated real-data NLDAS-arrival or changed-PRISM incremental acceptance."""
import argparse
import json
import os
import shutil
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from benchmark_nrt_extension import clone, compare, receipt
from netCDF4 import Dataset
from validate_nrt_gfs_cycle import check_reference_times

from hydro_ops.forcing import complete_day
from hydro_ops.forcing import nrt_cycle as cycle
from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.source_selection import select_hourly_source


def snapshot(root):
    return {str(p): cycle.identity(p) for p in root.glob('*/*/*.LDASIN_DOMAIN1')}


def perturb_precipitation(path):
    with Dataset(path, 'r+') as data:
        values = data['ppt'][:]
        wet = ~np.ma.getmaskarray(values) & np.isfinite(np.ma.getdata(values)) & (np.ma.getdata(values) > 0)
        if not wet.any():
            raise ValueError('No wet PRISM cells to perturb')
        values[wet] *= 1.01
        data['ppt'][:] = values
    return int(wet.sum())


def perturb_temperature(path, variable):
    with Dataset(path, 'r+') as data:
        values = data[variable][:]
        valid = ~np.ma.getmaskarray(values) & np.isfinite(np.ma.getdata(values))
        if not valid.any():
            raise ValueError('No valid PRISM temperature cells')
        values[valid] += 1.0
        data[variable][:] = values
    return int(valid.sum())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('nldas', 'prism', 'temperature'), required=True)
    parser.add_argument('--campaign', required=True, type=Path)
    parser.add_argument('--arrival-days', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.relative_to(root / 'forcing/work')
    campaign.mkdir(parents=True, exist_ok=False)
    work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    target = date(2026, 8, 25) if args.scenario == 'nldas' else date(2026, 9, 15)
    arrival_days = [target - timedelta(days=i) for i in reversed(range(args.arrival_days))]
    if args.scenario != 'nldas' and args.arrival_days != 1:
        parser.error('--arrival-days requires --scenario nldas')
    as_of = datetime.now(UTC)
    report = {'status': 'running', 'scenario': args.scenario, 'job': os.environ['SLURM_JOB_ID'],
              'target': str(target), 'scope': 'controlled source change; private files; setup/reference excluded from update time'}
    output = campaign / 'acceptance.json'
    _atomic_json(output, report)
    private_prism = campaign / 'prism'
    baseline_events = []

    class Engine(cycle.RecentNrt):
        def prism_paths(self, day):
            return [private_prism / var / f'{d:%Y/%m}/prism_{var}_us_25m_{d:%Y%m%d}.nc'
                    for d in (day, day + timedelta(days=1)) for var in ('ppt', 'tmin', 'tmax')]

        def baseline_day(self, day):
            path = cycle.day_path(self.baseline, day)
            old = cycle.identity(path)
            result = super().baseline_day(day)
            baseline_events.append({'day': str(day), 'rebuilt': old != cycle.identity(path)})
            return result

    originals = {}
    try:
        cycle.require_operational_gfs(root)
        for offset in range(-args.arrival_days, 2):
            day = target + timedelta(days=offset)
            for var in ('ppt', 'tmin', 'tmax'):
                relative = Path(var) / f'{day:%Y/%m}/prism_{var}_us_25m_{day:%Y%m%d}.nc'
                source = root / 'forcing/inputs/oregon_state/prism/an/4km/daily' / relative
                originals[str(source)] = cycle.identity(source)
                destination = private_prism / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        os.environ['HYDRO_OPS_PRISM_DATA_DIR'] = str(private_prism)
        seed_source = (root / 'forcing/work/nrt-gfs-cycle-validation/job_4551871' if args.scenario == 'nldas'
                       else root / 'forcing/work/nrt-extension-20260919T021605')
        offsets = (-1, 1) if args.scenario == 'nldas' else (-2, -1, 0, 1)
        if args.arrival_days > 1:
            offsets = ()  # Build support days too; do not depend on historical test artifacts.
        for offset in offsets:
            day = target + timedelta(days=offset)
            source = cycle.day_path(seed_source / 'baseline', day)
            originals[str(source)] = cycle.identity(source)
            clone(source, cycle.day_path(campaign / 'baseline', day))
        engine = Engine(root, work / 'seed', as_of, baseline_root=campaign / 'baseline', output_root=campaign / 'nrt')
        days = arrival_days if args.scenario == 'nldas' else [target - timedelta(days=1), target]

        def before_arrival(valid, nldas, hrrr):
            return select_hourly_source(valid, nldas, hrrr,
                preference=('hrrr',) if valid.date() in arrival_days else ('nldas2', 'hrrr'))

        started = time.monotonic()
        if args.scenario == 'nldas':
            cycle.select_hourly_source = complete_day.select_hourly_source = before_arrival
        try:
            report['seed'] = [engine.produce_day(day) for day in days]
        finally:
            cycle.select_hourly_source = complete_day.select_hourly_source = select_hourly_source
        report['seed_seconds'] = time.monotonic() - started
        if any(not item['prism_constrained'] for item in report['seed']):
            raise ValueError('Expected PRISM constraints in both scenarios')
        _atomic_json(output, report)
        if args.scenario == 'nldas':
            for day in days:
                check_reference_times(cycle.day_path(engine.output, day), 24)
        old_final = campaign / 'before.LDASIN_DOMAIN1'
        shutil.copy2(cycle.day_path(engine.output, target), old_final)
        before = snapshot(engine.baseline)
        final_before = snapshot(engine.output)
        if args.scenario == 'prism':
            revised = engine.prism_paths(target)[3]  # Next window ppt: does not affect previous calendar day.
            report['perturbed_cells'] = perturb_precipitation(revised)
        elif args.scenario == 'temperature':
            report['perturbed_cells'] = {
                var: perturb_temperature(engine.prism_paths(target)[index], var)
                for var, index in (('tmin', 4), ('tmax', 5))
            }
        baseline_events.clear()
        engine.work = work / 'update'
        engine.work.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        report['update'] = [engine.produce_day(day) for day in days]
        report['update_seconds'] = time.monotonic() - started
        report['baseline_calls'] = list(baseline_events)
        rebuilt = sorted({e['day'] for e in baseline_events if e['rebuilt']})
        expected = [str(day) for day in arrival_days] if args.scenario == 'nldas' else []
        if rebuilt != expected:
            raise ValueError(f'Unexpected baseline rebuilds: {rebuilt}')
        for path, old in before.items():
            if ((Path(path).name[:8] not in {d.strftime('%Y%m%d') for d in arrival_days} or args.scenario != 'nldas')
                    and cycle.identity(Path(path)) != old):
                raise ValueError('Unrelated baseline changed')
        final = cycle.day_path(engine.output, target)
        if args.scenario == 'nldas':
            for day in days:
                checked = cycle.day_path(engine.output, day)
                check_reference_times(checked, 0)
                if cycle.read_json(receipt(checked))['hourly_primary_sources'] != ['nldas2'] * 24:
                    raise ValueError('NLDAS did not fully replace HRRR/GFS')
        else:
            previous = cycle.day_path(engine.output, target - timedelta(days=1))
            if cycle.identity(previous) != final_before[str(previous)]:
                raise ValueError('Unrelated final day changed')
            with Dataset(old_final) as a, Dataset(final) as b:
                changed = ('T2D', 'Q2D', 'LWDOWN') if args.scenario == 'temperature' else ('RAINRATE',)
                for variable in changed:
                    if np.ma.allequal(a[variable][:], b[variable][:]):
                        raise ValueError(f'Changed PRISM did not alter {variable}')
                if args.scenario == 'temperature' and not np.ma.allequal(a['RAINRATE'][:], b['RAINRATE'][:]):
                    raise ValueError('Temperature-only revision altered precipitation')
        report['stage_timings'] = cycle.read_json(receipt(final))['stage_timings']
        _atomic_json(output, report)
        # Fresh scratch/output and explicitly disabled window cache provide an
        # independent reconciliation reference. NLDAS also rebuilds its target baseline.
        reference_root = campaign / 'reference-baseline'
        for path in engine.baseline.glob('*/*/*.LDASIN_DOMAIN1'):
            if args.scenario == 'nldas' and path.name[:8] in {d.strftime('%Y%m%d') for d in arrival_days}:
                continue
            clone(path, reference_root / path.relative_to(engine.baseline))
        os.environ['HYDRO_OPS_NRT_WINDOW_CACHE'] = ''
        reference = Engine(root, work / 'reference', as_of, baseline_root=reference_root, output_root=campaign / 'reference')
        started = time.monotonic()
        for day in days if args.scenario == 'nldas' else [target]:
            reference.produce_day(day)
            compare(cycle.day_path(reference.output, day), cycle.day_path(engine.output, day))
            if args.scenario == 'nldas':
                compare(cycle.day_path(reference.baseline, day), cycle.day_path(engine.baseline, day))
        report['reference_and_comparison_seconds'] = time.monotonic() - started
        repeated_before = {**snapshot(engine.baseline), **snapshot(engine.output)}
        started = time.monotonic()
        report['repeat'] = [engine.produce_day(day) for day in days]
        report['repeat_seconds'] = time.monotonic() - started
        if any(r['status'] != 'unchanged' for r in report['repeat']):
            raise ValueError('Repeat unexpectedly republished')
        if any(cycle.identity(Path(p)) != old for p, old in {**originals, **repeated_before}.items()):
            raise ValueError('Repeat or original files changed')
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        _atomic_json(output, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
