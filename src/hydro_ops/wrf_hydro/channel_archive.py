"""Lossless calendar collections of native one-hour or record-based CHRTOUT."""
import json
import tempfile
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import netCDF4 as nc
import numpy as np

STATIC_FEATURES = {"feature_id", "latitude", "longitude", "order", "elevation"}


def identity(path):
    stat = path.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def record_dimensions(name, variable):
    if name == "reference_time":
        return ("time",)
    if "time" in variable.dimensions:
        if variable.dimensions[0] != "time":
            raise ValueError(f"Unsupported nonleading time dimension: {name}")
        return variable.dimensions
    if "feature_id" in variable.dimensions and name not in STATIC_FEATURES:
        return ("time", *variable.dimensions)
    return variable.dimensions


def raw_record(variable, index, count):
    if "time" in variable.dimensions:
        return variable[index]
    if variable.name == "reference_time":
        values = np.asarray(variable[:]).reshape(-1)
        if values.size != 1:
            raise ValueError("Nonrecord reference time is not scalar")
        return values[0]
    if count != 1:
        raise ValueError(f"Unrecoverable multihour field without time: {variable.name}")
    return variable[:]


def publish_day(inputs, output):
    # Import here to keep the public production API backwards compatible.
    from hydro_ops.wrf_hydro.production import normalize_archive_time, times

    identities = [identity(path) for path in inputs]
    records = []
    schemas = None
    for path in inputs:
        with nc.Dataset(path) as source:
            stamps = times(source)
            if not stamps:
                raise ValueError(f"Empty hourly input: {path}")
            schema = {name: (var.dtype, record_dimensions(name, var))
                      for name, var in source.variables.items()}
            if schemas is not None and schema != schemas:
                raise ValueError(f"Channel schema changed: {path}")
            schemas = schema
            if "streamflow" not in schema or "time" not in schema['streamflow'][1]:
                raise ValueError("Missing time-dependent streamflow")
            for name, var in source.variables.items():
                if ("time" in schema[name][1] and "time" not in var.dimensions
                        and name != 'reference_time' and len(stamps) != 1):
                    raise ValueError(f"Unrecoverable multihour field without time: {name}")
            records.extend((path, i, len(stamps), stamp) for i, stamp in enumerate(stamps))
    stamps = [row[3] for row in records]
    if (not stamps or len(stamps) > 24 or stamps != sorted(set(stamps))
            or stamps[0].strftime('%Y%m%d') != output.name[:8]
            or any(t.date() != stamps[0].date() or t.minute or t.second for t in stamps)
            or any(b - a != timedelta(hours=1) for a, b in pairwise(stamps))):
        raise ValueError("Duplicate, gapped, unordered or non-calendar hourly records")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + '.', suffix='.partial', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with nc.Dataset(inputs[0]) as first, nc.Dataset(temporary, 'w', format='NETCDF4') as dest:
            first.set_auto_maskandscale(False)
            dest.setncatts({key: first.getncattr(key) for key in first.ncattrs()})
            for name, dim in first.dimensions.items():
                if name != 'reference_time':
                    dest.createDimension(name, None if name == 'time' else len(dim))
            for name, var in first.variables.items():
                dims = schemas[name][1]
                kwargs = {'fill_value': var.getncattr('_FillValue')} if '_FillValue' in var.ncattrs() else {}
                if dims and var.dtype.kind not in 'SUO':
                    kwargs.update(zlib=True, complevel=2, shuffle=True)
                    if dims == ('time', 'feature_id'):
                        kwargs['chunksizes'] = (1, min(65536, len(first.dimensions['feature_id'])))
                out = dest.createVariable(name, var.datatype, dims, **kwargs)
                out.setncatts({key: var.getncattr(key) for key in var.ncattrs() if key != '_FillValue'})
                out.set_auto_maskandscale(False)
                if 'time' not in dims:
                    out[:] = var[:]
            for index, (path, local, count, _) in enumerate(records):
                with nc.Dataset(path) as src:
                    src.set_auto_maskandscale(False)
                    for name, var in src.variables.items():
                        out = dest[name]
                        for key in ('units', 'calendar', '_FillValue', 'scale_factor', 'add_offset'):
                            if (key in var.ncattrs()) != (key in out.ncattrs()):
                                raise ValueError(f'Encoding attribute changed: {name}/{key}')
                            if key in var.ncattrs():
                                np.testing.assert_array_equal(var.getncattr(key), out.getncattr(key))
                        if 'time' in out.dimensions:
                            out[index] = raw_record(var, local, count)
                        else:
                            np.testing.assert_array_equal(var[:], out[:])
            # Refresh metadata only after verifying the actual record times.
            normalize_archive_time(dest, stamps)
            dest.storage_grouping = 'calendar day 00-23 UTC'
            dest.temporal_resolution = 'hourly'
            dest.calendar_day_complete = int(len(stamps) == 24)
            dest.channel_archive_policy = 'explicit_record_stack_v1'
            dest.channel_archive_validation = 'all variables, every record, raw equality'
        # Independent reopen and full raw-value comparison. No timestamp-only acceptance.
        with nc.Dataset(temporary) as dest:
            if times(dest) != stamps:
                raise ValueError('Published timestamp mismatch')
            dest.set_auto_maskandscale(False)
            for index, (path, local, count, _) in enumerate(records):
                with nc.Dataset(path) as src:
                    src.set_auto_maskandscale(False)
                    for name, var in src.variables.items():
                        out = dest[name]
                        if out.dimensions != schemas[name][1]:
                            raise ValueError(f'Published dimension mismatch: {name}')
                        if 'time' in out.dimensions:
                            np.testing.assert_array_equal(out[index], raw_record(var, local, count))
                        else:
                            np.testing.assert_array_equal(out[:], var[:])
        if identities != [identity(path) for path in inputs]:
            raise ValueError('Channel source changed during publication')
        temporary.replace(output)
        receipt = output.with_name(output.name + '.archive.json')
        partial = receipt.with_name(receipt.name + '.partial')
        partial.write_text(json.dumps({'status': 'passed', 'policy': 'explicit_record_stack_v1',
            'records': len(records), 'all_variable_values_verified': True,
            'sources': [{'path': str(p), 'identity': i} for p, i in zip(inputs, identities)],
            'published_identity': identity(output)}, indent=2) + '\n')
        partial.replace(receipt)
    finally:
        temporary.unlink(missing_ok=True)


def publish_hourly(paths, destination, ncrcat=None):
    """ncrcat is retained only for API compatibility; no NCO concatenation occurs."""
    from hydro_ops.wrf_hydro.production import times

    groups = {}
    for path in paths:
        groups.setdefault(path.name[:8], []).append(path)
    for day, inputs in groups.items():
        output = destination / day[:4] / day[4:6] / f'{day}.CHRTOUT_DOMAIN1'
        if output.exists():
            with nc.Dataset(output) as data:
                old = times(data)
            if len(old) != 1 or old[0].hour != 0:
                raise ValueError(f'Refusing overlap with existing archive: {output}')
            inputs = [output, *inputs]
        publish_day(inputs, output)
