"""Read-only coverage study: native NLDAS-2 history and genuinely rebuilt NWM samples."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.coverage import geographic_domain_mask

NAMES = {"T2D": "Tair", "Q2D": "Qair", "PSFC": "PSurf", "LWDOWN": "LWdown",
         "SWDOWN": "SWdown", "U2D": "Wind_E", "V2D": "Wind_N", "RAINRATE": "Rainf"}


def valid_values(variable, values):
    raw = np.ma.getdata(values)
    valid = ~np.ma.getmaskarray(values) & np.isfinite(raw)
    for attribute in ("_FillValue", "missing_value"):
        if attribute in variable.ncattrs():
            valid &= raw != variable.getncattr(attribute)
    return valid & (raw > -1.e20) & (raw < 1.e20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("native", "target"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    samples, missing = [], []
    if args.mode == "native":
        root = project / "forcing/inputs/nasa/nldas2/fora0125_hourly_v2.0"
        for year in range(1979, 2027):
            for month in (1, 4, 7, 10):
                day = date(year, month, 15)
                if day > date(2026, 9, 12):
                    continue
                path = root / f"{year}/NLDAS_FORA0125_H.A{day:%Y%m%d}.020.nc"
                if path.is_file():
                    samples.append(("native", str(day), path))
                else:
                    missing.append(str(path))
        domain = land = None
    else:
        for month in range(1, 5):
            stamp = f"1979{month:02}15"
            root = project / "forcing/work/restore-water-v3" / stamp[:6]
            for kind in ("baseline", "retro"):
                samples.append((f"1979_{kind}", stamp,
                                root / kind / "1979" / stamp[4:6] / f"{stamp}.LDASIN_DOMAIN1"))
        for month in range(3, 7):
            stamp = f"2021{month:02}15"
            samples.append(("2021_retro", stamp, project / "forcing/outputs/conus/retro/2021"
                            / stamp[4:6] / f"{stamp}.LDASIN_DOMAIN1"))
        model = project / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc"
        with Dataset(model) as data:
            latitude, longitude = data["XLAT"][0].data, data["XLONG"][0].data
            land = np.asarray(data["XLAND"][0]) == 1
        domain = geographic_domain_mask(latitude, longitude)
        assert not np.any(land & ~domain)
    states = {}
    provenance = []
    reference_grid = None
    for group, day, path in samples:
        with Dataset(path) as data:
            coordinates = (np.asarray(data["lat"][:]), np.asarray(data["lon"][:]))
            if args.mode == "native":
                if reference_grid is None:
                    reference_grid = coordinates
                for first, second in zip(reference_grid, coordinates, strict=True):
                    np.testing.assert_array_equal(first, second)
                indices = list(range(len(data.dimensions["time"])))
            else:
                np.testing.assert_allclose(coordinates[0], latitude, atol=1e-5, rtol=0)
                np.testing.assert_allclose(coordinates[1], longitude, atol=1e-5, rtol=0)
                if getattr(data, "forcing_domain_policy", "") != "nldas2_active_gaps_preserve_inactive_v3":
                    raise ValueError(f"Sample does not have v3 coverage: {path}")
                if getattr(data, "forcing_source", "") != "nldas2":
                    raise ValueError(f"Sample is not NLDAS-2 based: {path}")
                indices = [0, 6, 12, 18]
            provenance.append({"group": group, "day": day, "path": str(path),
                               "indices": indices,
                               "domain_policy": str(getattr(data, "forcing_domain_policy", "native"))})
            for output_name, native_name in NAMES.items():
                variable = data[native_name if args.mode == "native" else output_name]
                # Native daily fields are small: read once to avoid repeated decompression.
                native_values = variable[:] if args.mode == "native" else None
                key = group + "__" + output_name
                for index in indices:
                    values = native_values[index] if native_values is not None else variable[index]
                    valid = valid_values(variable, values)
                    if domain is not None:
                        valid &= domain
                    if key not in states:
                        states[key] = {"union": valid.copy(), "intersection": valid.copy(),
                                       "patterns": Counter(), "records": 0,
                                       "min_valid": int(valid.sum()), "max_valid": int(valid.sum()),
                                       "missing_active_max": 0}
                    state = states[key]
                    state["union"] |= valid
                    state["intersection"] &= valid
                    state["patterns"][hashlib.sha256(np.packbits(valid).tobytes()).hexdigest()] += 1
                    state["records"] += 1
                    count = int(valid.sum())
                    state["min_valid"] = min(count, state["min_valid"])
                    state["max_valid"] = max(count, state["max_valid"])
                    if land is not None:
                        state["missing_active_max"] = max(state["missing_active_max"], int((land & ~valid).sum()))
        print(json.dumps({"mode": args.mode, "group": group, "day": day,
                          "completed_files": len(provenance)}), flush=True)
    summary = {"mode": args.mode, "files": provenance, "missing_files": missing, "statistics": {}}
    arrays = {}
    for key, state in states.items():
        union, intersection = state["union"], state["intersection"]
        summary["statistics"][key] = {
            k: v for k, v in state.items() if k not in ("union", "intersection", "patterns")
        }
        summary["statistics"][key].update({
            "unique_masks": len(state["patterns"]), "pattern_counts": dict(state["patterns"]),
            "union_cells": int(union.sum()), "intersection_cells": int(intersection.sum()),
            "variable_coverage_cells": int((union & ~intersection).sum()),
        })
        if land is not None:
            summary["statistics"][key]["inactive_union_cells"] = int((union & ~land).sum())
        arrays[key + "__union"] = union
        arrays[key + "__intersection"] = intersection
    if args.mode == "target":
        arrays.update(land=land, domain=domain)
        summary["grid"] = {"active_cells": int(land.sum()), "domain_cells": int(domain.sum()),
                           "inactive_domain_cells": int((domain & ~land).sum())}
        comparisons = {}
        for name in NAMES:
            a = states["1979_baseline__" + name]["union"]
            b = states["1979_retro__" + name]["union"]
            c = states["2021_retro__" + name]["union"]
            comparisons[name] = {"baseline_vs_monthly_prism_xor": int((a ^ b).sum()),
                                 "baseline_vs_2021_xor": int((a ^ c).sum()),
                                 "1979_only": int((a & ~c).sum()), "2021_only": int((c & ~a).sum())}
        summary["comparisons"] = comparisons
        candidates = {}
        for label, names in (("thermodynamic", ("T2D", "Q2D", "PSFC", "LWDOWN")),
                             ("seven_meteorological", tuple(n for n in NAMES if n != "RAINRATE")),
                             ("all_eight", tuple(NAMES))):
            candidate = land.copy()
            for key, state in states.items():
                if key.split("__")[1] in names:
                    candidate |= state["union"]
            candidate &= domain
            arrays["candidate__" + label] = candidate
            candidates[label] = {"cells": int(candidate.sum()),
                                 "inactive_cells": int((candidate & ~land).sum()),
                                 "excluded_inactive_domain_cells": int((domain & ~candidate).sum())}
        summary["candidates"] = candidates
    np.savez_compressed(args.output / f"{args.mode}_masks.npz", **arrays)
    (args.output / f"{args.mode}_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"completed": args.mode, "files": len(provenance),
                      "missing_files": len(missing)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
