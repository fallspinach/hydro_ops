"""Opt-in scratch rebuild of a baseline day from exact native inputs."""

import json
import os
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.forcing.complete_day import produce_complete_day, utc_hours
from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.native_donor import NativeDonorRepair
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.pilot_schema import ensure_precipitation_timing
from hydro_ops.forcing.source_provenance import refresh_source_summary
from hydro_ops.forcing.source_selection import select_hourly_source


def main():
    root = Path(__file__).resolve().parents[1]
    pilot = root / "forcing/work/native-donor-pilot-20260825"
    day = date(2026, 8, 24) + timedelta(days=int(os.environ.get("SLURM_ARRAY_TASK_ID", "1")))
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
    scratch.mkdir(parents=True, exist_ok=True)
    layout = replace(OperationalLayout.project_defaults(root), nldas2_root=pilot / "inputs/nldas2")
    destination = pilot / "baseline" / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")
    if destination.exists():
        raise FileExistsError(destination)
    produce_complete_day(day, layout, scratch / "hourly", work_directory=scratch,
                         assembly_workers=4, precipitation_remap_workers=1)
    repair = NativeDonorRepair(layout, root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc",
        root / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc", maximum_km=40)
    reports, paths = [], []
    for valid in utc_hours(day):
        path = scratch / "hourly" / valid.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1")
        selected = select_hourly_source(valid, layout.nldas2_root, layout.hrrr_root)
        report = {"valid_time": valid.isoformat(), "source": str(selected.path), "product": selected.product,
                  "repairs": repair.repair(path, selected)}
        print(json.dumps(report), flush=True)
        reports.append(report)
        ensure_precipitation_timing(path)
        paths.append(path)
    create_daily_archive(paths, destination, day, work_directory=scratch,
                         global_attributes={"native_donor_pilot": "20260825", "forcing_stream": "baseline"})
    with Dataset(destination, "r+") as data:
        refresh_source_summary(data)
    destination.with_name(destination.name + ".native-audit.json").write_text(json.dumps(
        {"status": "passed", "day": day.isoformat(), "maximum_native_distance_km": repair.maximum_km,
         "hourly": reports, "note": "All active fields reopened and verified before daily aggregation"}, indent=2) + "\n")
    print(f"BASELINE PASSED {destination}", flush=True)


if __name__ == "__main__":
    main()
