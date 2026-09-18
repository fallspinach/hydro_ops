"""Experimental lossless record assembly for compatible compressed NetCDF files.

Opt-in value overrides are written and fully read back; other chunks are copied.
"""
from __future__ import annotations

import hashlib
import itertools
import os
import shutil
import tempfile
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import h5py
import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.daily_archive import (
    _attributes,
    _chunks,
    _digest,
    _source_variable,
    _validate_inputs,
)


class UnsupportedArchive(ValueError):
    """Encoding needs the existing value-based archive writer."""


def digest_file(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def assemble(paths, indices, destination, day, work, *, expected_hours=24,
             overrides=None, global_attributes=None, normalize_precipitation_timing=False,
             preserve_source_chunks=False):
    started = time.perf_counter()
    paths = list(map(Path, paths))
    destination = Path(destination)
    if destination.exists() or destination.resolve() in {p.resolve() for p in paths}:
        raise ValueError('Experimental archive requires a new separate destination')
    dims, names = _validate_inputs(paths, expected_hours, indices, normalize_precipitation_timing)
    normalized = {"precip_timing_source_id"} & set(names) if normalize_precipitation_timing else set()
    overrides = {} if overrides is None else overrides
    if set(overrides)-set(names):
        raise ValueError('Unknown override variables')
    unique = list(dict.fromkeys(paths))
    identities = {p: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in unique}
    work.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='chunk-archive-', dir=work) as temp:
        partial = Path(temp)/'archive.nc'
        with Dataset(paths[0]) as first, Dataset(partial, 'w', format='NETCDF4') as out:
            out.createDimension('time', expected_hours)
            for name, length in dims.items():
                out.createDimension(name, length)
            out.setncatts({k: first.getncattr(k) for k in first.ncattrs()})
            out.archive_period = day.isoformat()
            out.archive_granularity = 'daily'
            out.hourly_source_count = expected_hours
            out.history = f'{datetime.now(UTC).isoformat()} experimental compressed-chunk archive'
            out.setncatts(global_attributes or {})
            for name in names:
                src = _source_variable(first, name)
                options = {}
                if '_FillValue' in src.ncattrs():
                    options['fill_value'] = src._FillValue
                chunks = _chunks(src, {'time': expected_hours, **dims})
                if preserve_source_chunks and src.ndim >= 2 and hasattr(src, 'chunking'):
                    source_chunks = src.chunking()
                    if isinstance(source_chunks, (tuple, list)):
                        chunks = tuple(source_chunks)
                        if 'time' in src.dimensions and chunks[src.dimensions.index('time')] != 1:
                            raise UnsupportedArchive(f'Time chunks must contain one record: {name}')
                if chunks:
                    options.update(zlib=True, complevel=2, shuffle=True, chunksizes=chunks)
                var = out.createVariable(name, src.dtype, src.dimensions, **options)
                attrs = _attributes(src)
                if name in normalized:
                    attrs["long_name"] = "source supplying the within-block hourly timing pattern"
                    attrs["comment"] = "Zero means no separate within-block timing provenance (not reconciled or unavailable); legacy missing fields normalized to zero."
                # Recomputing vmin/vmax would require a separate reduction.
                if 'vmin' in attrs or 'vmax' in attrs:
                    raise UnsupportedArchive('Extrema metadata requires a value-reduction path')
                if name in overrides:
                    if 'time' not in src.dimensions:
                        raise ValueError('Static variable overrides are unsupported')
                    shape = list(src.shape)
                    shape[src.dimensions.index('time')] = expected_hours
                    if tuple(shape) != np.shape(overrides[name]):
                        raise ValueError(f'Invalid override shape: {name}')
                if name == 'time':
                    attrs = {k: v for k, v in attrs.items() if k not in ('begin_date', 'begin_time', 'end_date', 'end_time')}
                var.setncatts(attrs)
        receipts = []
        with ExitStack() as stack:
            sources = {p: stack.enter_context(h5py.File(p, 'r')) for p in unique}
            out = stack.enter_context(h5py.File(partial, 'r+'))
            for name in names:
                if name in overrides or name in normalized:
                    continue
                target = out[name]
                first = sources[paths[0]][name]
                with Dataset(paths[0]) as schema:
                    dimensions = schema[name].dimensions
                axis = dimensions.index('time') if 'time' in dimensions else None
                if axis is None and any(sources[p][name].chunks != target.chunks for p in unique):
                    values = first[...]
                    for p in unique:
                        np.testing.assert_array_equal(values, sources[p][name][...])
                    target[...] = values
                    receipts.append((name, None, None, hashlib.sha256(values.tobytes()).digest()))
                    continue
                for p in unique:
                    src = sources[p][name]
                    # Small time coordinates may have automatic unlimited-dimension
                    # chunks (e.g. 512), larger than the 24-record destination. They
                    # use the existing decoded-copy path, not raw chunk transfer.
                    decoded_coordinate = preserve_source_chunks and target.ndim == 1 and target.chunks is None
                    if src.dtype != first.dtype or (not decoded_coordinate and src.chunks != target.chunks):
                        raise UnsupportedArchive(f'Incompatible dtype/chunks: {name}; source={src.chunks}, target={target.chunks}')
                    a, b = src.id.get_create_plist(), target.id.get_create_plist()
                    if not decoded_coordinate and [a.get_filter(i) for i in range(a.get_nfilters())] != [b.get_filter(i) for i in range(b.get_nfilters())]:
                        raise UnsupportedArchive(f'Incompatible filters: {name}')
                    # Metadata differences must not be hidden by raw copying.
                    for key in ('_FillValue', 'scale_factor', 'add_offset', 'units'):
                        if (key in first.attrs) != (key in src.attrs):
                            raise ValueError(f'Metadata mismatch: {name}/{key}')
                        if key in first.attrs:
                            np.testing.assert_array_equal(first.attrs[key], src.attrs[key])
                if target.chunks:
                    if axis is not None and target.chunks[axis] != 1:
                        raise UnsupportedArchive('Time chunks must contain one record')
                    for index, (p, source_index) in enumerate(zip(paths, indices, strict=True)):
                        if axis is None and index:
                            continue
                        spans = [range(0, n, c) if j != axis else [source_index]
                                 for j, (n, c) in enumerate(zip(first.shape, first.chunks, strict=True))]
                        for origin in itertools.product(*spans):
                            offset = list(origin)
                            if axis is not None:
                                offset[axis] = index
                            offset = tuple(offset)
                            flags, raw = sources[p][name].id.read_direct_chunk(origin)
                            if axis is None:
                                for other in unique:
                                    if sources[other][name].id.read_direct_chunk(origin) != (flags, raw):
                                        raise ValueError(f'Static chunk mismatch: {name}')
                            target.id.write_direct_chunk(offset, raw, flags)
                            receipts.append((name, offset, flags, hashlib.sha256(raw).digest()))
                else:
                    if axis is None:
                        values = first[...]
                        for p in unique:
                            np.testing.assert_array_equal(values, sources[p][name][...])
                    else:
                        values = np.concatenate([np.take(sources[p][name][...], [i], axis=axis)
                                                 for p, i in zip(paths, indices, strict=True)], axis=axis)
                    target[...] = values
                    receipts.append((name, None, None, hashlib.sha256(values.tobytes()).digest()))
        with Dataset(partial, 'a') as output:
            for name, values in overrides.items():
                variable = output[name]
                axis = variable.dimensions.index('time')
                for index in range(expected_hours):
                    selection = [slice(None)]*variable.ndim
                    selection[axis] = index
                    variable[tuple(selection)] = np.ma.masked_invalid(np.asanyarray(values)[tuple(selection)])
            # This small categorical field is decoded explicitly: legacy absence
            # means unknown (zero), never an invented precipitation donor. Copy
            # every existing record unchanged, including its missing-value mask.
            normalized_receipts = []
            for name in normalized - set(overrides):
                target = output[name]
                axis = target.dimensions.index('time')
                with ExitStack() as stack:
                    sources = {p: stack.enter_context(Dataset(p)) for p in unique}
                    for index, (p, source_index) in enumerate(zip(paths, indices, strict=True)):
                        selection = [slice(None)] * target.ndim
                        selection[axis] = source_index
                        values = _source_variable(sources[p], name)[tuple(selection)]
                        selection[axis] = index
                        target[tuple(selection)] = values
                        normalized_receipts.append((name, tuple(selection), _digest(values)))
        written = time.perf_counter()-started
        with h5py.File(partial, 'r') as check:
            for name, offset, flags, expected in receipts:
                if offset is None:
                    actual = hashlib.sha256(check[name][...].tobytes()).digest()
                else:
                    actual_flags, raw = check[name].id.read_direct_chunk(offset)
                    if flags != actual_flags:
                        raise ValueError('Copied filter flags differ')
                    actual = hashlib.sha256(raw).digest()
                if actual != expected:
                    raise ValueError(f'Archive integrity check failed: {name}')
        with Dataset(partial) as output:
            for name, selection, expected in normalized_receipts:
                if _digest(output[name][selection]) != expected:
                    raise ValueError(f'Normalized provenance read-back differs: {name}')
            for name, values in overrides.items():
                variable = output[name]
                axis = variable.dimensions.index('time')
                for index in range(expected_hours):
                    selection = [slice(None)]*variable.ndim
                    selection[axis] = index
                    expected = np.ma.masked_invalid(np.asanyarray(values)[tuple(selection)].astype(variable.dtype))
                    if _digest(expected) != _digest(variable[tuple(selection)]):
                        raise ValueError(f'Override read-back differs: {name}/{index}')
        verified = time.perf_counter()-started-written
        for p, before in identities.items():
            if (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) != before:
                raise ValueError('Source changed during archive assembly')
        publishing = destination.with_name(destination.name+'.part')
        if publishing.exists():
            raise FileExistsError(publishing)
        shutil.copyfile(partial, publishing)
        with publishing.open('rb') as handle:
            os.fsync(handle.fileno())
        if digest_file(partial) != digest_file(publishing):
            raise ValueError('Archive transfer checksum mismatch')
        publishing.replace(destination)
    return {'write_seconds': written, 'integrity_seconds': verified,
            'normalized_variables': sorted(normalized), 'preserve_source_chunks': preserve_source_chunks,
            'total_seconds': time.perf_counter()-started, 'status': 'passed'}
