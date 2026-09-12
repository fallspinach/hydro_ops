"""Restore retired-v2 inactive cells from a newly reconstructed PRISM forcing day."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import repair_nwm_forcing_domain as domain
from netCDF4 import Dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("reconstructed", type=Path)
    parser.add_argument("--work-directory", required=True, type=Path)
    parser.add_argument("--backup-directory", required=True, type=Path)
    parser.add_argument("--model-mask", type=Path,
                        default=Path("nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc"))
    args = parser.parse_args()
    model = args.model_mask
    with Dataset(args.target) as original:
        if getattr(original, "forcing_domain_policy", "") != "nldas2_rectangle_nwm_active_land_v2":
            print(f"SKIP not retired-v2: {args.target}")
            return 0
    before = domain.completeness(args.target, model)
    if any(before["missing_model_land"].values()):
        raise ValueError("Original active land has missing values; restoration cannot certify preservation")
    args.work_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="restore-water-", dir=args.work_directory) as temp:
        staged = Path(temp) / args.target.name
        shutil.copy2(args.target, staged)
        with Dataset(staged, "r+") as output, Dataset(args.reconstructed) as source:
            if (getattr(source, "forcing_domain_policy", "") != domain.ACTIVE_POLICY
                    or str(getattr(source, "prism_reconciliation_accepted", "false")).lower() != "true"):
                raise ValueError("Reconstruction lacks accepted PRISM and current spatial policy")
            for name in ("lat", "lon", "time"):
                np.testing.assert_array_equal(output[name][:], source[name][:])
            if output["time"].units != source["time"].units:
                raise ValueError("Time units differ")
            latitude, longitude = output["lat"][:], output["lon"][:]
            land = domain.load_model_land_mask(model, latitude, longitude)
            inside = domain.geographic_domain_mask(latitude, longitude)
            for name in domain.VARIABLES:
                for index in range(output[name].shape[0]):
                    values = np.ma.getdata(output[name][index]).copy()
                    reconstructed = np.ma.asarray(source[name][index]).filled(output[name]._FillValue)
                    values[inside & ~land] = reconstructed[inside & ~land]
                    output[name][index] = values
            output.setncattr("forcing_domain_policy", "water_source_restored_pending_audit")
            output.setncattr("inactive_restoration_source", str(args.reconstructed.resolve()))
            output.setncattr("inactive_restoration_method", "reconstruct from archived sources and monthly PRISM; preserve original active cells exactly")
            for key in ("forcing_inactive_masked_counts", "forcing_active_mask_rule"):
                if key in output.ncattrs():
                    output.delncattr(key)
        report = domain.repair(staged, staged, model_mask_path=model, preserve_active=True)
        if before["active_sha256"] != report["active_sha256"]:
            raise ValueError("Restoration changed active forcing")
        args.backup_directory.mkdir(parents=True, exist_ok=True)
        backup = args.backup_directory / args.target.name
        if not backup.exists():
            shutil.copy2(args.target, backup)
        partial = args.target.with_name(args.target.name + ".restore-water.part")
        shutil.copy2(staged, partial)
        partial.replace(args.target)
        print(json.dumps({"path": str(args.target), "status": "inactive_values_reconstructed",
                          "active_values_unchanged": True, "audit": report}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
