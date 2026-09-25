"""Opt-in efficiency prototypes, preserving production science and validations."""
import hashlib
import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.native_donor import FIELDS
from hydro_ops.forcing.operations import discover_precipitation_candidates, discover_stage4_six_hour
from hydro_ops.forcing.precipitation_cache import assets, records
from hydro_ops.forcing.precipitation_day import process_precipitation_day


def same_input_content(left, right):
    """Conservative candidate publication check; history alone is non-scientific.

    Caller retains the existing destination (including mtime) if true. This never
    updates an operational fingerprint from a timestamp alone.
    """
    left, right = Path(left), Path(right)
    def checksum(path):
        with path.open('rb') as handle:
            return hashlib.file_digest(handle, 'sha256').digest()
    if left.stat().st_size == right.stat().st_size and checksum(left) == checksum(right):
        return True
    def attrs(variable):
        return {k: variable.getncattr(k) for k in variable.ncattrs() if k != 'history'}
    def equal_attrs(a, b):
        if a.keys() != b.keys():
            return False
        try:
            for k in a:
                np.testing.assert_equal(a[k], b[k])
        except AssertionError:
            return False
        return True
    with Dataset(left) as a, Dataset(right) as b:
        if ({k: len(v) for k, v in a.dimensions.items()} !=
                {k: len(v) for k, v in b.dimensions.items()} or set(a.variables) != set(b.variables)):
            return False
        if not equal_attrs(attrs(a), attrs(b)):
            return False
        for name, x in a.variables.items():
            y = b[name]
            if (x.dtype, x.dimensions, x.shape) != (y.dtype, y.dimensions, y.shape):
                return False
            if not equal_attrs(attrs(x), attrs(y)):
                return False
            x.set_auto_maskandscale(False)
            y.set_auto_maskandscale(False)
            for index in range(x.shape[0]) if x.ndim else [Ellipsis]:
                try:
                    np.testing.assert_array_equal(x[index], y[index])
                except AssertionError:
                    return False
    return True


def prepare_precipitation(day, layout, directory, work, workers):
    directory.mkdir(parents=True, exist_ok=False)
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=5)
    times = [start + timedelta(hours=i) for i in range(30)]
    found = [discover_precipitation_candidates(t, layout) for t in times]
    candidates, quality = [v[0] for v in found], [v[1] for v in found]
    products = set().union(*(set(c) for c in candidates))
    weights = {p: layout.mrms_conservative if p.startswith('mrms_') else
               layout.stage4_conservative if p.startswith('stage4_') else
               layout.nldas2_conservative if p == 'nldas2' else layout.hrrr_conservative
               for p in products}
    constraints = {t: p for t in times if (p := discover_stage4_six_hour(t, layout)) is not None}
    def static():
        return assets(weights, layout.target_grid, layout.remap_grid,
                      layout.mrms_quality_bilinear, layout.stage4_conservative, layout.cnrfc_nwm_mask)
    before, fixed = records(times, candidates, quality, constraints), static()
    outputs = process_precipitation_day(times, candidates, quality, weights, layout.target_grid,
        layout.remap_grid, directory / 'outputs', work_directory=work,
        quality_weights=layout.mrms_quality_bilinear if any(quality) else None,
        remap_workers=workers, stage4_six_hour_paths=constraints,
        stage4_six_hour_weights=layout.stage4_conservative, cnrfc_mask_path=layout.cnrfc_nwm_mask)
    if before != records(times, candidates, quality, constraints) or fixed != static():
        raise ValueError('Precipitation inputs changed while caching')
    from hydro_ops.forcing.precipitation_cache import identity
    (directory / 'cache.json').write_text(json.dumps({'inputs': before, 'static': fixed,
        'outputs': {t.isoformat(): identity(p) for t, p in zip(times, outputs, strict=True)}}))


@contextmanager
def shared_mixed_precipitation():
    """Patch only this isolated process, not operational workers."""
    original = nrt_cycle.produce_complete_day
    def produce(day, layout, output, **kwargs):
        if (kwargs.get('start_hour', 0), kwargs.get('end_hour', 23)) != (0, 23):
            cache = kwargs['work_directory'] / 'mixed-precipitation-cache'
            if not (cache / 'cache.json').exists():
                started = time.monotonic()
                prepare_precipitation(day, layout, cache, kwargs['work_directory'],
                                      kwargs.get('precipitation_remap_workers', 1))
                print(json.dumps({'stage': 'shared_mixed_precipitation_prepare',
                                  'seconds': time.monotonic() - started}), flush=True)
            kwargs['precipitation_cache'] = cache
        return original(day, layout, output, **kwargs)
    nrt_cycle.produce_complete_day = produce
    try:
        yield
    finally:
        nrt_cycle.produce_complete_day = original


def audit_final_sparse(engine, path, day, constrained):
    """Same full validation; rewrite only records whose raw values need changes."""
    writes = 0
    with Dataset(path, 'r+') as data:
        times = num2date(data['time'][:], data['time'].units, only_use_cftime_datetimes=False)
        if [(t.date(), t.hour, t.minute, t.second) for t in times] != [(day, h, 0, 0) for h in range(24)]:
            raise ValueError('NRT output is not 00–23 UTC')
        if not getattr(data, 'cnrfc_stage4_policy', ''):
            raise ValueError('NRT output lost CNRFC exclusion provenance')
        if constrained and str(getattr(data, 'prism_reconciliation_accepted', 'false')).lower() != 'true':
            raise ValueError('NRT output lacks accepted PRISM reconciliation')
        for index in range(24):
            for name in FIELDS:
                variable = data[name]
                values = np.ma.filled(variable[index], np.nan)
                if not np.isfinite(values[engine.repair.active]).all():
                    raise ValueError(f'Final NRT active holes: {index} {name}')
                values[~engine.repair.keep] = np.nan
                desired = np.where(np.isfinite(values), values, variable._FillValue)
                # Compare the unmasked record to retain reference-writer behavior
                # for NaN/Inf and nonstandard missing-value representations too.
                variable.set_auto_mask(False)
                actual = variable[index]
                variable.set_auto_mask(True)
                if not np.array_equal(actual, desired, equal_nan=True):
                    variable[index] = desired
                    writes += 1
        data.forcing_stream = 'nrt'
        data.archive_granularity = 'utc_calendar_day'
        data.nrt_production_policy = nrt_cycle.POLICY
        data.prism_constraint_status = 'applied' if constrained else 'awaiting_complete_daily_inputs'
        if not constrained:
            data.prism_reconciliation_accepted = 'false'
        data.forcing_domain_policy = 'nrt_native_gfs_static_envelope_v1'
    with Dataset(path) as data:
        for index in range(24):
            for name in FIELDS:
                values = np.ma.filled(data[name][index], np.nan)
                if (not np.isfinite(values[engine.repair.active]).all()
                        or np.isfinite(values[~engine.repair.keep]).any()):
                    raise ValueError(f'NRT readback failed: {index} {name}')
    return writes
