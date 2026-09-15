"""Compare lossless chunk-copy calendar assembly with the current archive writer."""
import json
import os
import time
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.chunk_archive import assemble
from hydro_ops.forcing.daily_archive import create_daily_archive


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    result = root/'forcing/work/chunk-calendar-benchmark'/f'job_{job}'
    result.mkdir(parents=True, exist_ok=False)
    scratch = Path('/scratch')/os.environ['SLURM_JOB_USER']/f'job_{job}'
    scratch.mkdir(parents=True, exist_ok=True)
    original = root/'forcing/work/post2020-production-benchmark-20260913T154753/reference/2021/09'
    # Prepare authentic 12–11 time windows from already validated calendar data.
    # This preparation is excluded from both measured assembly methods.
    windows = []
    for day in (4, 5):
        paths = [original/f'202109{day-1:02}.LDASIN_DOMAIN1']*12+[original/f'202109{day:02}.LDASIN_DOMAIN1']*12
        window = scratch/f'window-{day}.nc'
        assemble(paths, list(range(12, 24))+list(range(12)), window, date(2021, 9, day), scratch)
        windows.append(window)
    paths = [windows[0]]*12+[windows[1]]*12
    indices = list(range(12, 24))+list(range(12))
    day = date(2021, 9, 4)
    report = {'scope': 'isolated calendar assembly only', 'status': 'running'}
    t = time.perf_counter()
    create_daily_archive(paths, result/'reference.nc', day, work_directory=scratch,
                         source_time_indices=indices, verification='targeted')
    report['reference_seconds'] = time.perf_counter()-t
    print(json.dumps(report), flush=True)
    report['chunks'] = assemble(paths, indices, result/'chunks.nc', day, scratch)
    print(json.dumps(report), flush=True)
    # Fully decoded independent comparison against both method and original day.
    with Dataset(result/'reference.nc') as a, Dataset(result/'chunks.nc') as b, Dataset(original/'20210904.LDASIN_DOMAIN1') as c:
        for data in (a, b, c):
            data.set_auto_maskandscale(False)
        if set(a.variables) != set(b.variables) or set(a.variables) != set(c.variables):
            raise ValueError('Variable sets differ')
        for name, var in a.variables.items():
            if var.dimensions != b[name].dimensions or var.dtype != b[name].dtype:
                raise ValueError('Variable schema differs')
            for attr in var.ncattrs():
                np.testing.assert_array_equal(var.getncattr(attr), b[name].getncattr(attr))
            steps = range(var.shape[0]) if var.dimensions and var.dimensions[0] == 'time' else [Ellipsis]
            for step in steps:
                expected = np.asarray(var[step]).tobytes()
                if expected != np.asarray(b[name][step]).tobytes() or expected != np.asarray(c[name][step]).tobytes():
                    raise ValueError(f'Values differ: {name}/{step}')
    report['status'] = 'passed'
    (result/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
