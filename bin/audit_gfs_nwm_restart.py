"""Audit terminal CONUS land states, including active GFS northern-gap cells."""

import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, chartostring


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("restart", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--expected-time", default="2026-08-24_23:00:00")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with Dataset(root / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc") as data:
        active = np.asarray(data["XLAND"][0]) == 1
    gap = np.zeros(active.shape, bool)
    with np.load(root / "forcing/work/gfs-nrt-exploration/gfs_hrrr_gap_weights_v1.npz") as geometry:
        gap.ravel()[geometry["indices"]] = True
    northern = active & gap
    report = {"restart": str(args.restart.resolve()), "status": "pending",
              "active_cells": int(active.sum()), "northern_active_cells": int(northern.sum()), "fields": {}}
    if not northern.any():
        raise ValueError("No northern model cells found")
    with Dataset(args.restart) as data:
        stamp = str(chartostring(data["Times"][:])[0])
        report["timestamp"] = stamp
        if stamp != args.expected_time:
            raise ValueError(f"Unexpected terminal restart time: {stamp}")
        for name in ("SOIL_T", "SMC", "SH2O", "TG", "TV", "TAH", "ACCPRCP"):
            var = data[name]
            stats = {region: {"count": 0, "missing": 0, "minimum": float("inf"), "maximum": -float("inf")}
                     for region in ("all_active", "northern_active")}
            for start in range(0, active.shape[0], 128):
                stop = min(start+128, active.shape[0])
                values = np.ma.asarray(var[0, start:stop, ...])
                if values.ndim == 3:
                    values = np.moveaxis(values, 1, -1)  # y, layer, x -> y, x, layer
                for region, mask in (("all_active", active), ("northern_active", northern)):
                    selected = values[mask[start:stop]].reshape(-1)
                    invalid = np.ma.getmaskarray(selected) | ~np.isfinite(np.ma.getdata(selected))
                    # Some restart writers use only a global missing_value attribute.
                    for missing in (getattr(data, "missing_value", None), getattr(var, "missing_value", None)):
                        if missing is not None:
                            invalid |= np.isin(np.ma.getdata(selected), np.asarray(missing))
                    s = stats[region]
                    s["count"] += int(selected.size)
                    s["missing"] += int(invalid.sum())
                    finite = np.ma.getdata(selected)[~invalid]
                    if finite.size:
                        s["minimum"] = min(s["minimum"], float(finite.min()))
                        s["maximum"] = max(s["maximum"], float(finite.max()))
            report["fields"][name] = stats
    report["status"] = "passed" if all(s["missing"] == 0 for fields in report["fields"].values() for s in fields.values()) else "failed"
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if report["status"] != "passed":
        raise ValueError("Missing active terminal states; see restart audit")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
