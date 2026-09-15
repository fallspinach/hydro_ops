"""Isolated raw-chunk masking benchmark; never publishes production files.

Run on a compute node. Unchanged HDF5 chunks retain their compressed bytes;
excluded chunks reuse an encoded fill block. Independently compare all values.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import time
from pathlib import Path

import h5py
import numpy as np
from netCDF4 import Dataset
from test_static_forcing_mask import FIELDS, digest, missing

from hydro_ops.forcing.coverage import geographic_domain_mask


def chunk_mask(source, mask_path, output):
    source, mask_path, output = map(Path, (source, mask_path, output))
    if output.exists() or source.resolve() == output.resolve():
        raise ValueError("Destination must be a new separate file")
    before = source.stat()
    started = time.monotonic()
    with Dataset(mask_path) as mask:
        keep = np.asarray(mask['keep'][:], bool)
        active = np.asarray(mask['active'][:], bool)
        lat, lon = np.asarray(mask['lat'][:]), np.asarray(mask['lon'][:])
        if digest(keep) != mask.keep_sha256 or digest(active) != mask.active_sha256:
            raise ValueError('Mask checksum mismatch')
        if np.any(active & ~keep) or np.any(keep & ~geographic_domain_mask(lat, lon)):
            raise ValueError('Unsafe static mask')
    # NetCDF creates the dimension scales/attributes; h5py only supplies data.
    with Dataset(source) as src:
        if any(d.isunlimited() for d in src.dimensions.values()):
            raise ValueError('Prototype requires fixed dimensions')
        if not set(FIELDS).issubset(src.variables):
            raise ValueError('Missing forcing fields')
        for name, coords in [('lat', lat), ('lon', lon)]:
            np.testing.assert_allclose(src[name][:], coords, rtol=0, atol=1e-5)
        with Dataset(output, 'w', format='NETCDF4') as dst:
            dst.setncatts({k: src.getncattr(k) for k in src.ncattrs()})
            for name, dim in src.dimensions.items():
                dst.createDimension(name, len(dim))
            for name, var in src.variables.items():
                options = {}
                if '_FillValue' in var.ncattrs():
                    options['fill_value'] = var._FillValue
                filters = var.filters() or {}
                if any(filters.get(k) for k in ('szip', 'zstd', 'bzip2', 'blosc')):
                    raise ValueError('Unsupported compression')
                if filters.get('zlib'):
                    options.update(zlib=True, complevel=filters['complevel'], shuffle=filters['shuffle'])
                if filters.get('fletcher32'):
                    options['fletcher32'] = True
                if isinstance(var.chunking(), list):
                    options['chunksizes'] = var.chunking()
                out = dst.createVariable(name, var.dtype, var.dimensions, **options)
                out.setncatts({k: var.getncattr(k) for k in var.ncattrs() if k != '_FillValue'})
    counts = {'copied': 0, 'boundary': 0, 'excluded': 0}
    with h5py.File(source, 'r') as src, h5py.File(output, 'r+') as dst:
        for name in src:
            if name not in dst or not isinstance(src[name], h5py.Dataset):
                raise ValueError(f'Unsupported source structure: {name}')
            inp, out = src[name], dst[name]
            if name not in FIELDS:
                if inp.chunks:
                    for idx in range(inp.id.get_num_chunks()):
                        offset = inp.id.get_chunk_info(idx).chunk_offset
                        flags, raw = inp.id.read_direct_chunk(offset)
                        out.id.write_direct_chunk(offset, raw, flags)
                else:
                    out[...] = inp[...]
                continue
            if inp.ndim != 3 or inp.chunks is None or inp.chunks[0] != 1 or inp.shape[1:] != keep.shape:
                raise ValueError(f'Unsupported forcing chunks: {name}')
            # Raw copying requires an identical HDF5 filter pipeline.
            a, b = inp.id.get_create_plist(), out.id.get_create_plist()
            if [a.get_filter(i) for i in range(a.get_nfilters())] != [b.get_filter(i) for i in range(b.get_nfilters())]:
                raise ValueError('Filter mismatch')
            if inp.id.get_num_chunks() != np.prod([int(np.ceil(n/c)) for n, c in zip(inp.shape, inp.chunks)]):
                raise ValueError('Sparse forcing dataset unsupported in prototype')
            fill = inp.attrs['_FillValue']
            encoded_fill = None
            for t, y, x in itertools.product(range(inp.shape[0]), range(0, inp.shape[1], inp.chunks[1]), range(0, inp.shape[2], inp.chunks[2])):
                offset = (t, y, x)
                region = (slice(t, t+1), slice(y, min(y+inp.chunks[1], inp.shape[1])), slice(x, min(x+inp.chunks[2], inp.shape[2])))
                k = keep[region[1:]]
                if k.all():
                    flags, raw = inp.id.read_direct_chunk(offset)
                    out.id.write_direct_chunk(offset, raw, flags)
                    counts['copied'] += 1
                elif not k.any() and k.shape == inp.chunks[1:]:
                    if encoded_fill is None:
                        out[region] = np.full(inp.chunks, fill, dtype=inp.dtype)
                        dst.flush()
                        encoded_fill = out.id.read_direct_chunk(offset)
                    else:
                        flags, raw = encoded_fill
                        out.id.write_direct_chunk(offset, raw, flags)
                    counts['excluded'] += 1
                else:
                    values = inp[region]
                    values[:, ~k] = fill
                    out[region] = values
                    counts['boundary'] += 1
            dst.flush()
            print(json.dumps({'written': name, 'seconds': time.monotonic()-started}), flush=True)
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during benchmark')
    return {'write_seconds': time.monotonic()-started, 'chunks': counts, 'output_bytes': output.stat().st_size}


def verify(source, mask, output):
    started = time.monotonic()
    with Dataset(mask) as grid:
        keep, active = np.asarray(grid['keep'][:], bool), np.asarray(grid['active'][:], bool)
    with Dataset(source) as src, Dataset(output) as dst:
        src.set_auto_maskandscale(False)
        dst.set_auto_maskandscale(False)
        assert set(src.variables) == set(dst.variables)
        for name, var in src.variables.items():
            other = dst[name]
            assert var.dimensions == other.dimensions and var.dtype == other.dtype
            assert var.chunking() == other.chunking() and var.filters() == other.filters()
            for attr in var.ncattrs():
                np.testing.assert_array_equal(var.getncattr(attr), other.getncattr(attr))
            indices = range(var.shape[0]) if var.dimensions and var.dimensions[0] == 'time' else [Ellipsis]
            for index in indices:
                expected = np.asarray(var[index]).copy()
                if name in FIELDS:
                    if np.any(missing(var, expected) & active):
                        raise ValueError(f'Source missing active data: {name}')
                    expected[~keep] = var._FillValue
                actual = np.asarray(other[index])
                # Byte-exact check also preserves NaN payloads and signed zeros.
                if hashlib.sha256(expected.tobytes()).digest() != hashlib.sha256(actual.tobytes()).digest():
                    raise ValueError(f'Value mismatch: {name}/{index}')
    return time.monotonic()-started


def verify_chunks(source, mask, output):
    """Integrity only: does not certify the source's active-cell completeness.

    Raw equality proves unchanged compressed chunks. Decode changed boundary
    chunks and each distinct excluded encoding, rather than all retained data.
    This is not a replacement for source validation when its audit is absent.
    """
    started = time.monotonic()
    with Dataset(mask) as grid:
        keep = np.asarray(grid['keep'][:], bool)
        active = np.asarray(grid['active'][:], bool)
        if digest(keep) != grid.keep_sha256 or digest(active) != grid.active_sha256:
            raise ValueError('Mask checksum mismatch')
        if np.any(active & ~keep) or np.any(keep & ~geographic_domain_mask(grid['lat'][:], grid['lon'][:])):
            raise ValueError('Unsafe static mask')
    with Dataset(source) as src, Dataset(output) as dst:
        if set(src.variables) != set(dst.variables) or set(src.dimensions) != set(dst.dimensions):
            raise ValueError('Schema mismatch')
        if set(src.ncattrs()) != set(dst.ncattrs()):
            raise ValueError('Global attributes mismatch')
        for attr in src.ncattrs():
            np.testing.assert_array_equal(src.getncattr(attr), dst.getncattr(attr))
        for name, dim in src.dimensions.items():
            if (len(dim), dim.isunlimited()) != (len(dst.dimensions[name]), dst.dimensions[name].isunlimited()):
                raise ValueError('Dimension mismatch')
        for name, var in src.variables.items():
            other = dst[name]
            if (var.dimensions != other.dimensions or var.dtype != other.dtype
                    or var.chunking() != other.chunking() or var.filters() != other.filters()
                    or set(var.ncattrs()) != set(other.ncattrs())):
                raise ValueError(f'Variable schema mismatch: {name}')
            for attr in var.ncattrs():
                np.testing.assert_array_equal(var.getncattr(attr), other.getncattr(attr))
    counts = {'raw_equal': 0, 'boundary': 0, 'excluded': 0, 'excluded_decoded': 0}
    with h5py.File(source, 'r') as src, h5py.File(output, 'r') as dst:
        for name in src:
            inp, out = src[name], dst[name]
            if not inp.chunks:
                if inp[...].tobytes() != out[...].tobytes():
                    raise ValueError(f'Contiguous values differ: {name}')
                continue
            a, b = inp.id.get_create_plist(), out.id.get_create_plist()
            if [a.get_filter(i) for i in range(a.get_nfilters())] != [b.get_filter(i) for i in range(b.get_nfilters())]:
                raise ValueError('Filter mismatch')
            if inp.id.get_num_chunks() != out.id.get_num_chunks():
                raise ValueError('Chunk allocation mismatch')
            fills = set()
            for idx in range(inp.id.get_num_chunks()):
                offset = inp.id.get_chunk_info(idx).chunk_offset
                region = tuple(slice(o, min(o+c, n)) for o, c, n in zip(offset, inp.chunks, inp.shape))
                if name not in FIELDS or keep[region[1:]].all():
                    if inp.id.read_direct_chunk(offset) != out.id.read_direct_chunk(offset):
                        raise ValueError(f'Copied chunk differs: {name}/{offset}')
                    counts['raw_equal'] += 1
                    continue
                k = keep[region[1:]]
                if not k.any():
                    raw = out.id.read_direct_chunk(offset)
                    key = (raw[0], hashlib.sha256(raw[1]).digest(), k.shape)
                    if key not in fills:
                        actual = out[region]
                        expected = np.full(actual.shape, inp.attrs['_FillValue'], dtype=inp.dtype)
                        if actual.tobytes() != expected.tobytes():
                            raise ValueError(f'Excluded chunk not missing: {name}/{offset}')
                        fills.add(key)
                        counts['excluded_decoded'] += 1
                    counts['excluded'] += 1
                else:
                    expected = inp[region]
                    expected[:, ~k] = inp.attrs['_FillValue']
                    if expected.tobytes() != out[region].tobytes():
                        raise ValueError(f'Boundary chunk differs: {name}/{offset}')
                    counts['boundary'] += 1
    return {'seconds': time.monotonic()-started, 'counts': counts,
            'scope': 'transformation_integrity_only_not_source_validation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'mask', 'work'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    output = args.work/'chunk-masked.nc'
    report = chunk_mask(args.source, args.mask, output)
    report['chunk_verification'] = verify_chunks(args.source, args.mask, output)
    print(json.dumps({'chunk_verification': report['chunk_verification']}), flush=True)
    report['verification_seconds'] = verify(args.source, args.mask, output)
    report['status'] = 'passed'
    (args.work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
