"""Paired, elevation-consistent NLDAS-2/HRRR diagnostics at gap and seam samples."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, date2num

from hydro_ops.download.gfs import GfsDownloader
from hydro_ops.forcing.gfs_gap import bilinear_weights, lambert_coordinates
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.physics import lambert_grid_x_angle, rotate_grid_to_earth
from hydro_ops.forcing.thermodynamics import finalize_target_state, prepare_reference_state

NAMES = {
    "nldas2": {"T2D": "Tair", "Q2D": "Qair", "PSFC": "PSurf", "LWDOWN": "LWdown", "SWDOWN": "SWdown",
                "U2D": "Wind_E", "V2D": "Wind_N", "RAINRATE": "Rainf"},
    "hrrr": {"T2D": "TMP_2maboveground", "Q2D": "SPFH_2maboveground", "PSFC": "PRES_surface",
             "LWDOWN": "DLWRF_surface", "SWDOWN": "DSWRF_surface", "U2D": "UGRD_10maboveground",
             "V2D": "VGRD_10maboveground", "RAINRATE": "APCP_surface"},
}


def finalize_donors(fields, heights, weights, target_height):
    state = prepare_reference_state(fields["T2D"], fields["PSFC"], fields["Q2D"], fields["LWDOWN"],
        heights, relative_humidity_tolerance=0.2, reject_material_rh_excursions=False)
    def remap(a):
        return (a * weights).sum(axis=1)
    target = finalize_target_state(remap(state.temperature), remap(state.pressure),
        remap(state.relative_humidity), remap(state.longwave_factor), target_height,
        relative_humidity_tolerance=0.2)
    return {"T2D": target.temperature, "PSFC": target.pressure, "Q2D": target.specific_humidity,
            "LWDOWN": target.downward_longwave, **{name: remap(fields[name]) for name in ("SWDOWN", "U2D", "V2D")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "forcing/work/gfs-nrt-exploration"
    layout = OperationalLayout.project_defaults(root)
    with Dataset(root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc") as data:
        lat, lon = np.asarray(data["lat"][:]), np.asarray(data["lon"][:])
        active = np.asarray(data["active"][:], bool)
    with Dataset(layout.hrrr_elevation) as data:
        hlat, hlon = np.asarray(data["latitude"][:]), np.asarray(data["longitude"][:])
        hheight = np.ma.filled(data["HGT_surface"][0], np.nan)
    sx, sy = lambert_coordinates(hlat, hlon)
    ny, nx = sx.shape
    x0, y0 = sx[0, 0], sy[0, 0]
    dx, dy = (sx[0, -1]-x0)/(nx-1), (sy[-1, 0]-y0)/(ny-1)
    tx, ty = lambert_coordinates(lat, lon)
    ix, iy = (tx-x0)/dx, (ty-y0)/dy
    seam = np.flatnonzero(active & (iy >= ny-16) & (iy <= ny-1) & (ix >= 0) & (ix <= nx-1))
    with np.load(output / "gfs_hrrr_gap_weights_v1.npz") as geometry:
        gap = geometry["indices"][geometry["active"]]
    rng = np.random.default_rng(194)
    selected = np.concatenate([rng.choice(gap, min(args.samples, len(gap)), replace=False),
                               rng.choice(seam, min(args.samples, len(seam)), replace=False)])
    labels = np.array(["gap"] * min(args.samples, len(gap)) + ["seam_inside"] * min(args.samples, len(seam)))
    latitude, longitude = lat.ravel()[selected], lon.ravel()[selected]
    with Dataset(layout.target_elevation) as data:
        height = np.ma.filled(data["elevation"][:], np.nan).ravel()[selected]
    with Dataset(root / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc") as data:
        height = np.where(np.isfinite(height), height, np.asarray(data["HGT"][0]).ravel()[selected])
    with Dataset(layout.nldas2_elevation) as data:
        nheight = np.ma.filled(data["NLDAS_elev"][:], np.nan).squeeze()
    del lat, lon, tx, ty, sx, sy
    downloader = GfsDownloader(output / "cache", args.work)
    differences = {}
    counts = {}
    for month in (1, 7):
        for offset in range(7):
            day = date(2026, month, 15) + timedelta(days=offset)
            for hour in (0, 6, 12, 18):
                valid = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
                gfs = downloader.hour(valid)
                ga, gw = bilinear_weights(gfs.lat.values, gfs.lon.values, latitude, longitude)
                gd = {name: np.asarray(gfs[name]).ravel()[ga] for name in ("T2D", "PSFC", "Q2D", "LWDOWN", "SWDOWN", "U2D", "V2D")}
                mapped_gfs = finalize_donors(gd, np.asarray(gfs.elevation).ravel()[ga], gw, height)
                for product in ("nldas2", "hrrr"):
                    path = (layout.nldas2_root / f"{day:%Y}/NLDAS_FORA0125_H.A{day:%Y%m%d}.020.nc"
                            if product == "nldas2" else layout.hrrr_root / f"{day:%Y/%m}/hrrr_forcing.{day:%Y%m%d}.nc")
                    with Dataset(path) as source:
                        times = np.asarray(source["time"][:])
                        matches = np.flatnonzero(np.isclose(times, date2num(valid, source["time"].units), rtol=0, atol=1e-5))
                        if len(matches) != 1:
                            raise ValueError(f"No exact reference hour: {path} {valid}")
                        if product == "nldas2":
                            la, lo = np.asarray(source["lat"][:]), np.asarray(source["lon"][:])
                            eligible = (latitude >= la[0]) & (latitude < la[-1]) & (longitude >= lo[0]) & (longitude < lo[-1])
                            address, weight = bilinear_weights(la, lo, latitude[eligible], longitude[eligible])
                            elevation = nheight.ravel()[address]
                        else:
                            eligible = labels == "seam_inside"
                            address, weight = bilinear_weights(np.arange(ny), np.arange(nx), iy.ravel()[selected][eligible], ix.ravel()[selected][eligible])
                            elevation = hheight.ravel()[address]
                        fields = {name: np.ma.filled(source[var][int(matches[0])], np.nan).ravel()[address]
                                  for name, var in NAMES[product].items() if name != "RAINRATE"}
                        if product == "hrrr":
                            angle = lambert_grid_x_angle(((hlon.ravel()[address]+180)%360)-180,
                                central_longitude=-97.5, standard_parallel_1=38.5, standard_parallel_2=38.5)
                            fields["U2D"], fields["V2D"] = rotate_grid_to_earth(fields["U2D"], fields["V2D"], angle)
                    reference = finalize_donors(fields, elevation, weight, height[eligible])
                    for group in ("gap", "seam_inside"):
                        points = labels[eligible] == group
                        if not points.any():
                            continue
                        for name in reference:
                            difference = mapped_gfs[name][eligible][points] - reference[name][points]
                            key = f"{month:02}/{product}/{group}/{name}"
                            differences.setdefault(key, []).append(difference[np.isfinite(difference)])
                            counts[key] = counts.get(key, 0) + len(difference)
                print(valid.isoformat(), flush=True)
    summary = {}
    for key, arrays in differences.items():
        values = np.concatenate(arrays)
        summary[key] = {"paired": len(values), "requested": counts[key], "bias_gfs_minus_reference": float(values.mean()),
                        "rmse": float(np.sqrt(np.mean(values**2))), "absolute_difference_p95": float(np.quantile(abs(values), 0.95))}
    (output / "overlap_comparison.json").write_text(json.dumps({"samples_per_group": args.samples, "statistics": summary,
        "notes": ["Spatially sampled paired diagnostics, not independent validation", "Both products adjusted to identical target elevation",
                  "GFS radiation is interval-mean; HRRR analysis radiation has different temporal semantics",
                  "NLDAS comparisons exclude unavailable native interpolation stencils; no donor extrapolation",
                  "Seam-inside samples are within approximately 45 km of the native HRRR northern center boundary"]}, indent=2) + "\n")


if __name__ == "__main__":
    main()
