"""Read-only cutover checks, including summary reuse and NWM date boundaries."""
import argparse
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.baseline_publication import accepted_baseline
from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.temporal_summary import summarize, summary_path
from hydro_ops.nwm_masks import load_masks
from hydro_ops.status_monitor import production_inventory
from hydro_ops.wrf_hydro.production import check_forcing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    streams = root/'forcing/outputs/conus'
    report = {'status': 'running', 'samples': []}
    for stream, stamp in [('retro','19790102'), ('retro','19810101'), ('retro','20020101'),
                          ('retro','20200701'), ('retro','20210412'), ('nrt','20260915'),
                          ('baseline','20260915')]:
        p = streams/stream/'hourly'/stamp[:4]/stamp[4:6]/f'{stamp}.LDASIN_DOMAIN1'
        with Dataset(p) as data:
            t = data['time']
            values = num2date(t[:], t.units)
            assert [v.strftime('%Y%m%d%H') for v in values] == [stamp+f'{h:02}' for h in range(24)]
            assert all(name in data.variables for name in ('T2D','Q2D','PSFC','U2D','V2D','RAINRATE','LWDOWN','SWDOWN'))
        if stream == 'baseline':
            assert accepted_baseline(p, date(2026,9,15))
        report['samples'].append(str(p))
    # The model's existing interface uses naive UTC datetimes.
    check_forcing(streams/'retro/hourly', datetime(1985,12,31), datetime(1986,1,1))  # noqa: DTZ001
    report['nwm_year_boundary'] = 'passed'
    reducers, names, units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
    # This is a reuse check only: summarize fails rather than overwriting a
    # stale summary. Assert existence first to prevent accidental generation.
    day = date(1981,1,1)
    summary = summary_path(streams/'retro/daily', day)
    assert summary.is_file()
    report['daily_reuse'] = summarize(streams/'retro/hourly', summary, day, date(1981,1,2),
        reducers, names, units, skip_existing=True, domain='conus', stream='retro')
    summary = summary_path(streams/'retro/monthly', day, monthly=True)
    assert summary.is_file()
    report['monthly_reuse'] = summarize(streams/'retro/daily', summary, day, date(1981,2,1),
        reducers, names, units, skip_existing=True, from_daily=True, domain='conus', stream='retro')
    report['production'] = {s: production_inventory(streams/s/'hourly') for s in ('baseline','nrt','retro')}
    window, masks, lat, lon = load_masks(root/'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc')
    ys = slice(window.south_north_start, window.south_north_end+1)
    xs = slice(window.west_east_start, window.west_east_end+1)
    with Dataset(streams/'retro/hourly/1981/01/19810101.LDASIN_DOMAIN1') as data:
        np.testing.assert_allclose(data['lat'][ys,xs], lat, atol=1e-5, rtol=0)
        np.testing.assert_allclose(data['lon'][ys,xs], lon, atol=1e-5, rtol=0)
        for name in ('T2D','Q2D','PSFC','U2D','V2D','RAINRATE','LWDOWN','SWDOWN'):
            values = np.ma.filled(data[name][0,ys,xs], np.nan)
            assert np.isfinite(values[masks['model_mask']]).all(), name
    report['cnrfc_crop_read'] = 'passed (grid alignment and eight-field active completeness at hour 00)'
    report['status'] = 'passed'
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'production'}, indent=2))


if __name__ == '__main__':
    main()
