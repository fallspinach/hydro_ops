#!/usr/bin/env python3
"""Validate NWM forcing coverage and optionally publish edge-repaired subset copies."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.coverage import fill_persistent_gaps, persistent_gap_mask

FORCING_VARIABLES = ("T2D", "Q2D", "PSFC", "SWDOWN", "LWDOWN", "U2D", "V2D", "RAINRATE")


def _slice(window: tuple[int, int, int, int] | None) -> tuple[slice, slice]:
    if window is None:
        return slice(None), slice(None)
    y0, y1, x0, x1 = window
    return slice(y0, y1 + 1), slice(x0, x1 + 1)


def _extract(source: Path, destination: Path, window: tuple[int, int, int, int] | None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    if window is None:
        shutil.copy2(source, partial)
    else:
        y0, y1, x0, x1 = window
        subprocess.run(
            [
                "ncks",
                "-O",
                "-L",
                "2",
                "-d",
                f"y,{y0},{y1}",
                "-d",
                f"x,{x0},{x1}",
                str(source),
                str(partial),
            ],
            check=True,
        )
    partial.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forcing", nargs="+", type=Path, help="time-ordered hourly/daily LDASIN files")
    parser.add_argument("--wrfinput", required=True, type=Path)
    parser.add_argument(
        "--window",
        type=int,
        nargs=4,
        metavar=("Y0", "Y1", "X0", "X1"),
        help="inclusive source-grid window; omit when forcing already matches wrfinput",
    )
    parser.add_argument("--output-dir", type=Path, help="publish repaired copies here")
    parser.add_argument("--max-fill-distance", type=int, default=25)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    files = sorted(args.forcing)
    y_slice, x_slice = _slice(tuple(args.window) if args.window else None)
    with Dataset(args.wrfinput) as static:
        active_land = np.asarray(static["XLAND"][0]) == 1

    missing_by_variable: dict[str, list[np.ndarray]] = defaultdict(list)
    record_locations: list[tuple[Path, int]] = []
    times: list[float] = []
    time_units: str | None = None
    issues: list[str] = []
    for path in files:
        with Dataset(path) as data:
            for name in FORCING_VARIABLES:
                if name not in data.variables:
                    issues.append(f"{path}: missing variable {name}")
                    continue
                variable = data[name]
                fill = variable.getncattr("_FillValue")
                for index in range(variable.shape[0]):
                    values = np.asarray(variable[index, y_slice, x_slice].data)
                    if values.shape != active_land.shape:
                        issues.append(
                            f"{path}:{name} shape {values.shape} does not match "
                            f"wrfinput {active_land.shape}"
                        )
                        break
                    missing_by_variable[name].append(values == fill)
            if "time" not in data.variables:
                issues.append(f"{path}: missing time coordinate")
                continue
            time = data["time"]
            units = getattr(time, "units", None)
            if time_units is None:
                time_units = units
            elif units != time_units:
                issues.append(f"{path}: inconsistent time units {units!r}")
            for index, value in enumerate(np.asarray(time[:]).ravel()):
                times.append(float(value))
                record_locations.append((path, index))

    record_count = len(record_locations)
    for name in FORCING_VARIABLES:
        if len(missing_by_variable[name]) != record_count:
            issues.append(
                f"{name}: found {len(missing_by_variable[name])} records; expected {record_count}"
            )
    if len(times) > 1 and not np.allclose(np.diff(times), 60.0):
        issues.append("forcing time coordinates are not continuous hourly records")

    missing_stack = None
    persistent = np.zeros(active_land.shape, dtype=bool)
    if not issues:
        missing_stack = np.asarray([missing_by_variable[name] for name in FORCING_VARIABLES])
        persistent = persistent_gap_mask(missing_stack) & active_land

    summary: dict[str, dict[str, int]] = {}
    repair_results: dict[tuple[int, str], tuple[np.ndarray, np.ndarray]] = {}
    if missing_stack is not None:
        for variable_index, name in enumerate(FORCING_VARIABLES):
            total = transient = maximum = 0
            for record_index in range(record_count):
                missing = missing_stack[variable_index, record_index]
                land_missing = missing & active_land
                transient += int(np.count_nonzero(land_missing & ~persistent))
                total += int(land_missing.sum())
                if args.output_dir:
                    path, local_index = record_locations[record_index]
                    with Dataset(path) as data:
                        source = np.asarray(data[name][local_index, y_slice, x_slice].data)
                    try:
                        result = fill_persistent_gaps(
                            source,
                            missing=missing,
                            active_land=active_land,
                            allowed=persistent,
                            max_distance=args.max_fill_distance,
                        )
                    except ValueError as error:
                        issues.append(f"{path}:{name}[{local_index}]: {error}")
                        continue
                    maximum = max(maximum, int(result.distance.max()))
                    repair_results[(record_index, name)] = (result.values, result.repaired)
            summary[name] = {
                "missing_active_land_values": total,
                "transient_unapproved_values": transient,
                "maximum_fill_distance_cells": maximum,
            }
            if transient:
                issues.append(f"{name}: found {transient} transient active-land gaps")

    if args.output_dir and not issues:
        output_records: dict[Path, list[tuple[int, int]]] = defaultdict(list)
        for record_index, (path, local_index) in enumerate(record_locations):
            output_records[path].append((record_index, local_index))
        for source, records in output_records.items():
            destination = args.output_dir / source.name
            _extract(source, destination, tuple(args.window) if args.window else None)
            with Dataset(destination, "r+") as output:
                combined = np.zeros((len(records), *active_land.shape), dtype=np.uint8)
                counts: dict[str, int] = {}
                for name in FORCING_VARIABLES:
                    counts[name] = 0
                    for output_index, (record_index, local_index) in enumerate(records):
                        values, repaired = repair_results[(record_index, name)]
                        output[name][local_index] = values
                        combined[output_index] |= repaired.astype(np.uint8)
                        counts[name] += int(repaired.sum())
                mask = output.createVariable(
                    "forcing_edge_fill_mask",
                    "u1",
                    ("time", "y", "x"),
                    zlib=True,
                    complevel=2,
                    fill_value=np.uint8(255),
                )
                mask[:] = combined
                mask.long_name = "persistent source-target coverage gap repaired for model input"
                mask.flag_values = np.asarray([0, 1], dtype=np.uint8)
                mask.flag_meanings = "original edge_filled"
                output.edge_fill_policy = "persistent active-land gaps only; nearest four-neighbor"
                output.edge_fill_max_distance_cells = args.max_fill_distance
                output.edge_fill_counts = json.dumps(counts, sort_keys=True)

    report = {
        "accepted": not issues,
        "issues": issues,
        "files": [str(path) for path in files],
        "record_count": record_count,
        "persistent_active_land_cells": int(persistent.sum()),
        "max_fill_distance_cells": args.max_fill_distance,
        "output_dir": str(args.output_dir) if args.output_dir else None,
        "variables": summary,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    partial = args.report.with_suffix(args.report.suffix + ".partial")
    partial.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    partial.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
