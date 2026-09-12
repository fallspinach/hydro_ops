"""PRISM-constrain the isolated baseline and gate the midnight NWM test."""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.native_donor import FIELDS
from hydro_ops.forcing.pilot_schema import ensure_precipitation_timing
from hydro_ops.forcing.source_provenance import refresh_source_summary


def main():
    root = Path(__file__).resolve().parents[1]
    pilot = root / "forcing/work/native-donor-pilot-20260825"
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    # Neighboring pilot days may predate optional-provenance normalization.
    # Dependencies ensure these isolated files are fully published, not being written.
    for stamp in ("20260824", "20260825", "20260826"):
        ensure_precipitation_timing(pilot / f"baseline/2026/08/{stamp}.LDASIN_DOMAIN1")
    windows = scratch / "windows/nrt"
    for day in ("2026-08-25", "2026-08-26"):
        subprocess.run([sys.executable, str(root / "bin/produce_prism_constrained_daily.py"),
            "--day", day, "--complete-root", str(pilot / "baseline"), "--output-root", str(windows),
            "--revision", "provisional", "--stream", "nrt", "--work-directory", str(scratch),
            "--archive-access", "direct", "--allow-legacy-12utc-output"], cwd=root, check=True)
    subprocess.run([sys.executable, str(root / "bin/materialize_calendar_forcing.py"),
        "--input-root", str(windows), "--output-root", str(pilot / "nrt"), "--start", "2026-08-25",
        "--days", "1", "--stream", "nrt", "--require-accepted-prism-windows", "--hierarchical",
        "--work-directory", str(scratch)], cwd=root, check=True)
    path = pilot / "nrt/2026/08/20260825.LDASIN_DOMAIN1"
    with Dataset(root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc") as data:
        keep, active = np.asarray(data["keep"][:], bool), np.asarray(data["active"][:], bool)
        lat, lon = np.asarray(data["lat"][:]), np.asarray(data["lon"][:])
    island = active & (lat > 28.8) & (lat < 29.3) & (lon > -118.5) & (lon < -118.1)
    audit = {"status": "pending", "hours": [], "island_cells": int(island.sum())}
    # This is a new isolated artifact: final masking cannot affect existing products.
    with Dataset(path, "r+") as data:
        modes = refresh_source_summary(data)
        if modes != ["nldas2"] * 24:
            raise ValueError(f"Unexpected hourly provenance: {modes}")
        times = num2date(data["time"][:], data["time"].units, only_use_cftime_datetimes=False)
        if [t.isoformat() for t in times] != [f"2026-08-25T{h:02}:00:00" for h in range(24)]:
            raise ValueError("Not an exact UTC calendar day")
        for hour in range(24):
            for name in FIELDS:
                values = np.ma.filled(data[name][hour], np.nan)
                if not np.isfinite(values[active]).all():
                    raise ValueError(f"PRISM reintroduced missing active values: {hour} {name}")
                values[~keep] = np.nan
                data[name][hour] = np.where(np.isfinite(values), values, data[name]._FillValue)
            distances = np.asarray(data["native_donor_distance_km"][hour])
            maximum = float(distances[active].max())
            if maximum > 40 or not np.isfinite(distances[active]).all():
                raise ValueError("Native donor distance gate failed")
            audit["hours"].append({"hour": hour, "source": modes[hour], "maximum_donor_km": maximum,
                                   "island_maximum_donor_km": float(distances[island].max())})
        data.forcing_domain_policy = "native_donor_static_envelope_v1"
    with Dataset(path) as data:
        for hour in range(24):
            for name in FIELDS:
                values = np.ma.filled(data[name][hour], np.nan)
                if not np.isfinite(values[active]).all() or np.isfinite(values[~keep]).any():
                    raise ValueError(f"Final readback failed: {hour} {name}")
    audit["status"] = "passed"
    path.with_name(path.name + ".native-acceptance.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps(audit), flush=True)


if __name__ == "__main__":
    main()
