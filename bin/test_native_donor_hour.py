"""Isolated real-data repair probe before the full baseline rebuild."""

import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.native_donor import NativeDonorRepair
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.source_selection import select_hourly_source

root = Path(__file__).resolve().parents[1]
pilot = root / "forcing/work/native-donor-pilot-20260825"
scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
scratch.mkdir(parents=True, exist_ok=True)
layout = replace(OperationalLayout.project_defaults(root), nldas2_root=pilot / "inputs/nldas2")
day = date(2026, 8, 25)
source = root / "forcing/outputs/conus/baseline/hourly/2026/08/20260825.LDASIN_DOMAIN1"
output = scratch / "probe.LDASIN_DOMAIN1"
create_daily_archive([source], output, day, expected_hours=1, source_time_indices=[0], work_directory=scratch)
repair = NativeDonorRepair(layout, root / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc",
    root / "nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc", maximum_km=40)
report = repair.repair(output, select_hourly_source(datetime(2026, 8, 25, tzinfo=UTC), layout.nldas2_root, layout.hrrr_root))
(pilot / "probe.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report), flush=True)
