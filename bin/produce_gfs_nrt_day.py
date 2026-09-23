"""Opt-in northern fallback on a separate daily NRT file; no cron changes."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from netCDF4 import Dataset, num2date

from hydro_ops.forcing.gfs_publication import publish_gfs_day
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.source_selection import select_hourly_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--historical-test", action="store_true")
    parser.add_argument("--publish-nrt", action="store_true", help="Explicitly allow a new production NRT destination")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    production = root / "forcing/outputs"
    if args.output.resolve().is_relative_to(production) and (
            not args.publish_nrt or args.historical_test
            or not args.output.resolve().is_relative_to(production / "conus/nrt/hourly")):
        parser.error("Production writes require --publish-nrt, a CONUS nrt path, and non-historical operation")
    layout = OperationalLayout.project_defaults(root)
    artifacts = root / "forcing/work/gfs-nrt-exploration"
    available = False
    if not args.historical_test:
        from datetime import UTC
        with Dataset(args.input) as data:
            times = num2date(data["time"][:], data["time"].units, only_use_cftime_datetimes=False)
        available = any(select_hourly_source(t.replace(tzinfo=UTC), layout.nldas2_root, layout.hrrr_root).product == "nldas2" for t in times)
    report = publish_gfs_day(args.input, args.output,
        root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc",
        artifacts / "gfs_hrrr_gap_weights_v1.npz", artifacts / "gfs_gap_conservative_v1.nc",
        artifacts / "cache", args.work, nldas_available=available,
        as_of=args.as_of, historical_test=args.historical_test)
    print(json.dumps({k: v for k, v in report.items() if k != "hours"}), flush=True)


if __name__ == "__main__":
    main()
