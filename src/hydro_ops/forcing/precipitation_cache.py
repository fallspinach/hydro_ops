"""Job-local precipitation cache for opt-in multi-day repair benchmarks.

Not a persistent shared cache: sources, settings, static assets, and candidate
identities must still match at every use. Publication remains the caller's job.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.operations import discover_precipitation_candidates, discover_stage4_six_hour
from hydro_ops.forcing.precipitation import CNRFC_STAGE4_POLICY_START
from hydro_ops.forcing.precipitation_day import process_precipitation_day


def identity(path):
    if path is None:
        return None
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns]


def records(times, candidates, quality, constraints):
    return {v.isoformat(): {'candidates': {k: identity(p) for k, p in c.items()},
                            'quality': identity(q), 'constraint': identity(constraints.get(v))}
            for v, c, q in zip(times, candidates, quality, strict=True)}


def assets(weights, target, remap, quality_weights, six_weights, mask):
    return {'weights': {k: identity(p) for k, p in weights.items()}, 'target': identity(target),
            'remap': identity(remap), 'quality': identity(quality_weights),
            'six': identity(six_weights), 'mask': identity(mask)}


def prepare(start: date, days: int, layout, directory: Path, work: Path, *, remap_workers=2,
            end_hour=None):
    if not 1 <= days <= 3:
        raise ValueError('Job-local cache is bounded to three days')
    directory.mkdir(parents=True, exist_ok=False)
    first = datetime.combine(start, datetime.min.time(), UTC)-timedelta(hours=5)
    if end_hour is not None and (days != 1 or not 0 <= end_hour <= 23):
        raise ValueError('Partial precipitation cache must cover one day')
    times = [first+timedelta(hours=i) for i in range(24*days+6 if end_hour is None else end_hour+6)]
    discovered = [discover_precipitation_candidates(v, layout) for v in times]
    candidates, quality = [d[0] for d in discovered], [d[1] for d in discovered]
    products = set().union(*(set(c) for c in candidates))
    weights = {p: layout.mrms_conservative if p.startswith('mrms_') else
               layout.stage4_conservative if p.startswith('stage4_') else
               layout.nldas2_conservative if p == 'nldas2' else layout.hrrr_conservative
               for p in products}
    constraints = {v: p for v in times if (p := discover_stage4_six_hour(v, layout)) is not None}
    static = assets(weights, layout.target_grid, layout.remap_grid, layout.mrms_quality_bilinear,
                    layout.stage4_conservative, layout.cnrfc_nwm_mask)
    before = records(times, candidates, quality, constraints)
    outputs = process_precipitation_day(times, candidates, quality, weights, layout.target_grid,
        layout.remap_grid, directory/'outputs', work_directory=work,
        quality_weights=layout.mrms_quality_bilinear if any(quality) else None,
        remap_workers=remap_workers, stage4_six_hour_paths=constraints,
        stage4_six_hour_weights=layout.stage4_conservative, cnrfc_mask_path=layout.cnrfc_nwm_mask)
    if records(times, candidates, quality, constraints) != before or assets(
            weights, layout.target_grid, layout.remap_grid, layout.mrms_quality_bilinear,
            layout.stage4_conservative, layout.cnrfc_nwm_mask) != static:
        raise ValueError('Inputs changed while preparing precipitation cache')
    record = {'inputs': before, 'static': static,
              'outputs': {v.isoformat(): identity(p) for v, p in zip(times, outputs, strict=True)}}
    (directory/'cache.json').write_text(json.dumps(record, indent=2)+'\n')


def reuse(directory, times, candidates, quality, weights, target, remap, output_directory=None, **kwargs):
    if kwargs.get('mrms_quality_threshold', 0.5) != 0.5 or kwargs.get('cnrfc_policy_start', CNRFC_STAGE4_POLICY_START) != CNRFC_STAGE4_POLICY_START:
        raise ValueError('Cached precipitation settings differ')
    record = json.loads((Path(directory)/'cache.json').read_text())
    current = records(times, candidates, quality, kwargs.get('stage4_six_hour_paths', {}))
    if any(record['inputs'].get(k) != v for k, v in current.items()):
        raise ValueError('Cached precipitation inputs differ')
    static = assets(weights, target, remap, kwargs.get('quality_weights'),
                    kwargs.get('stage4_six_hour_weights'), kwargs.get('cnrfc_mask_path'))
    expected = record['static']
    for key, value in static.items():
        if key == 'weights':
            if any(expected[key].get(k) != v for k, v in value.items()):
                raise ValueError('Cached precipitation weights differ')
        elif (key != 'quality' or value is not None) and expected[key] != value:
            raise ValueError('Cached precipitation static assets differ')
    outputs = []
    for valid in times:
        saved = record['outputs'][valid.isoformat()]
        path = Path(saved[0])
        path.resolve().relative_to(Path(directory).resolve())
        if identity(path) != saved:
            raise ValueError('Cached precipitation output changed')
        outputs.append(path)
    return outputs
