"""Opt-in GFS daily gap patches: experiments only, never publish to nrt/retro."""

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num

from hydro_ops.download.gfs import GfsDownloader
from hydro_ops.forcing.gfs_conservative import ConservativeGap, build_conservative
from hydro_ops.forcing.gfs_gap import build_geometry, remap_hour
from hydro_ops.forcing.operations import OperationalLayout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--prepare-conservative", action="store_true")
    parser.add_argument("--conservative", action="store_true")
    parser.add_argument("--day", type=date.fromisoformat, default=date(2026, 1, 15))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("forcing/work/gfs-nrt-exploration"))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    layout = OperationalLayout.project_defaults(project)
    args.root.mkdir(parents=True, exist_ok=True)
    geometry_path = args.root / "gfs_hrrr_gap_weights_v1.npz"
    downloader = GfsDownloader(args.root / "cache", args.work)
    conservative_path = args.root / "gfs_gap_conservative_v1.nc"
    if args.prepare_conservative:
        print(json.dumps(build_conservative(geometry_path, layout.target_grid, conservative_path, args.work)), flush=True)
        return
    if args.prepare:
        sample = downloader.hour(datetime(2026, 1, 15, 1, tzinfo=UTC))
        report = build_geometry(project / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc",
            layout.hrrr_elevation, layout.target_elevation, sample.lat.values, sample.lon.values, geometry_path,
            model_terrain=project / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc")
        (args.root / "geometry.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
        return
    output = args.root / ("patches_conservative" if args.conservative else "patches") / f"{args.day:%Y/%m}/gfs_gap.{args.day:%Y%m%d}.nc"
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    temporary = args.work / f"gfs_gap.{args.day:%Y%m%d}.nc"
    hours = []
    with np.load(geometry_path) as geometry, Dataset(temporary, "w") as dst:
        precipitation_remapper = ConservativeGap(conservative_path, geometry) if args.conservative else None
        dst.createDimension("time", 24)
        dst.createDimension("cell", len(geometry["indices"]))
        dst.createDimension("bounds", 2)
        time = dst.createVariable("time", "f8", ("time",))
        time.units = "hours since 1970-01-01 00:00:00"
        time.calendar = "standard"
        time.bounds = "time_bounds"
        bounds = dst.createVariable("time_bounds", "f8", ("time", "bounds"))
        bounds.units = time.units
        for name, key in (("target_index", "indices"), ("latitude", "latitude"),
                          ("longitude", "longitude"), ("active", "active"), ("terrain_from_model_HGT", "terrain_fallback")):
            values = geometry[key]
            var = dst.createVariable(name, "u1" if values.dtype == bool else values.dtype,
                                     ("cell",), zlib=True, complevel=2)
            var[:] = values
        cycle_var = dst.createVariable("forecast_reference_time", "f8", ("time",))
        cycle_var.units = time.units
        lead_var = dst.createVariable("forecast_lead_hours", "i2", ("time",))
        dst.setncatts({"status": "experimental_sparse_gap_patch_not_LDASIN", "source": "GFS short forecasts",
                      "envelope_sha256": str(geometry["envelope_sha256"]), "grid_shape": geometry["shape"],
                      "precipitation_remapping": "CDO conservative destarea" if args.conservative else "bilinear exploratory only; conservative remapping required before operational adoption",
                      "meteorological_remapping": "cached bilinear of reference-elevation coupled state; target elevation restored",
                      "time_grouping": "00-23 UTC labels; hourly interval-end accumulation/mean fields",
                      "wind_orientation": "earth_relative; U2D/V2D carry 10-m winds consistent with existing workflow",
                      "asof_status": "historical archive experiment, not a reconstruction of real-time publication availability"})
        variables = {}
        for index in range(24):
            valid = datetime(args.day.year, args.day.month, args.day.day, tzinfo=UTC) + timedelta(hours=index)
            source = downloader.hour(valid)
            values = remap_hour(source, geometry, precipitation_remapper=precipitation_remapper)
            time[index] = date2num(valid, time.units)
            bounds[index] = [time[index] - 1, time[index]]
            cycle_var[index] = date2num(datetime.fromisoformat(source.attrs["cycle"]), time.units)
            lead_var[index] = source.attrs["lead"]
            for name, array in values.items():
                if name not in variables:
                    var = dst.createVariable(name, "f4", ("time", "cell"), zlib=True, complevel=2,
                                             chunksizes=(1, min(65536, len(array))))
                    var.units = {"T2D": "K", "Q2D": "kg kg-1", "PSFC": "Pa", "LWDOWN": "W m-2",
                                 "SWDOWN": "W m-2", "U2D": "m s-1", "V2D": "m s-1", "RAINRATE": "kg m-2 s-1"}[name]
                    var.cell_methods = "time: mean" if name in ("RAINRATE", "LWDOWN", "SWDOWN") else "time: point"
                    variables[name] = var
                variables[name][index] = array
            report = {"valid_time": valid.isoformat(), "cycle": source.attrs["cycle"], "lead": int(source.attrs["lead"]),
                      "retrieved_utc": source.attrs["retrieved_utc"], "source_url": source.attrs["url"],
                      "source_windows": json.loads(source.attrs["windows"]),
                      "remote_last_modified": source.attrs.get("remote_last_modified", ""),
                      "roundoff_clipped": json.loads(source.attrs["negative_roundoff_clipped"]),
                      "ranges": {name: [float(a.min()), float(a.max())] for name, a in values.items()}}
            hours.append(report)
            print(json.dumps(report), flush=True)
    # Validate every serialized field before publishing the experimental patch.
    with Dataset(temporary) as check:
        for name in variables:
            for index in range(24):
                if not np.isfinite(check[name][index]).all() or np.ma.getmaskarray(check[name][index]).any():
                    raise ValueError("Incomplete serialized GFS patch")
    import shutil
    partial = output.with_suffix(".part.nc")
    shutil.copyfile(temporary, partial)
    os.replace(partial, output)
    output.with_suffix(".json").write_text(json.dumps({"day": args.day.isoformat(), "status": "passed",
                                                      "hours": hours, "output": str(output)}, indent=2) + "\n")
    temporary.unlink()


if __name__ == "__main__":
    main()
