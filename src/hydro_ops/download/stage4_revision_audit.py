"""Read-only content comparison for late Stage-IV republications."""
import hashlib

import numpy as np
from netCDF4 import Dataset


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def signature(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def compare_netcdf(left, right):
    """Compare decoded fields, masks, coordinates and variable semantics exactly."""
    changes = {}
    with Dataset(left) as a, Dataset(right) as b:
        if set(a.variables) != set(b.variables):
            return {'schema_changed': True}
        for name, var in a.variables.items():
            other = b[name]
            if (var.shape != other.shape or var.dimensions != other.dimensions or
                    any(getattr(var, k, None) != getattr(other, k, None)
                        for k in ('units', 'calendar', 'grid_mapping'))):
                changes[name] = {'schema_changed': True}
                continue
            av, bv = np.ma.asarray(var[:]), np.ma.asarray(other[:])
            am, bm = np.ma.getmaskarray(av), np.ma.getmaskarray(bv)
            mask_changes = int(np.count_nonzero(am != bm))
            valid = ~(am | bm)
            x, y = av.data[valid], bv.data[valid]
            equal = x == y
            if np.issubdtype(x.dtype, np.number) and np.issubdtype(y.dtype, np.number):
                equal = equal | (np.isnan(x) & np.isnan(y))
            values_changed = int(np.count_nonzero(~equal))
            if mask_changes or values_changed:
                changes[name] = {'mask_changed_cells': mask_changes,
                                 'value_changed_cells': values_changed}
                if values_changed and np.issubdtype(x.dtype, np.number):
                    delta = np.abs(x.astype(float) - y.astype(float))
                    finite = delta[np.isfinite(delta)]
                    changes[name]['max_abs_difference'] = float(finite.max()) if finite.size else None
    return changes
