"""Checkpointed monthly CONUS runs and fail-closed production publication.

The two-day acceptance run uses exactly the same runner as production. Model
state is never modified in place; only the nonchronological initialization copy
has its date and cumulative diagnostics reset.
"""

# Model/NetCDF clocks are deliberately timezone-naive UTC throughout this module.
# ruff: noqa: DTZ001
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import netCDF4 as nc
import numpy as np

ACCUMULATIONS = {
    "ACMELT",
    "ACSNOW",
    "ACCPRCP",
    "ACCECAN",
    "ACCEDIR",
    "ACCETRAN",
    "SFCRUNOFF",
    "UDRUNOFF",
}


def stamp(date):
    return date.strftime("%Y-%m-%d_%H:%M:%S")


def restart_names(date):
    return (
        date.strftime("RESTART.%Y%m%d%H_DOMAIN1"),
        date.strftime("HYDRO_RST.%Y-%m-%d_%H:%M_DOMAIN1"),
    )


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(data, indent=2, default=str) + "\n")
    temporary.replace(path)


def copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def dates(start, end):
    while start < end:
        yield start
        start += timedelta(days=1)


def months(start, end):
    while start < end:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        stop = min(end, following)
        yield start, stop
        start = stop


def times(dataset):
    var = dataset["time"]
    return [
        datetime(t.year, t.month, t.day, t.hour, t.minute, t.second)
        for t in nc.num2date(var[:], var.units, calendar=getattr(var, "calendar", "standard"))
    ]


def restart_check(paths, date):
    with nc.Dataset(paths[0]) as ds:
        actual = nc.chartostring(ds["Times"][:]).reshape(-1)[0]
        if str(actual) != stamp(date):
            raise ValueError(f"Land restart date: {actual}, expected {stamp(date)}")
        for name in ("SMC", "SH2O", "SOIL_T", "SNEQV"):
            if name not in ds.variables:
                raise ValueError(f"Missing restart state {name}")
    with nc.Dataset(paths[1]) as ds:
        if ds.Restart_Time != stamp(date):
            raise ValueError("Hydro restart date mismatch")
        for name in ("hlink", "qlink1", "qlink2", "z_gwsubbas"):
            values = np.ma.asarray(ds[name][:])
            if np.ma.count(values) == 0 or not np.isfinite(values.compressed()).all():
                raise ValueError(f"Invalid routing restart {name}")


def check_active_state(path, active, names):
    with nc.Dataset(path) as ds:
        for name in names:
            var = ds[name]
            values = np.ma.asarray(var[:])
            dims = list(var.dimensions)
            y = next(i for i, d in enumerate(dims) if d in {"y", "south_north"})
            x = next(i for i, d in enumerate(dims) if d in {"x", "west_east"})
            values = np.moveaxis(values, (y, x), (-2, -1))[..., active]
            if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                raise ValueError(f"Missing/nonfinite active state {name}: {path}")


def initialization(project, destination):
    date = datetime(1979, 1, 2)
    donor = project / "nwm/restarts/conus/retro/1986/01"
    sources = [donor / name for name in restart_names(datetime(1986, 1, 1))]
    targets = [destination / name for name in restart_names(date)]
    marker = destination / "initialization.json"
    if marker.exists():
        with nc.Dataset(sources[1]) as donor_ds, nc.Dataset(targets[1]) as target_ds:
            if target_ds.his_out_counts != donor_ds.his_out_counts:
                raise ValueError("Cached initialization changed the donor history counter")
        restart_check(targets, date)
        return targets
    destination.mkdir(parents=True, exist_ok=True)
    for i, (source, target) in enumerate(zip(sources, targets)):
        temporary = target.with_name(target.name + ".partial")
        shutil.copy2(source, temporary)
        with nc.Dataset(temporary, "r+") as ds:
            ds.initialization_donor = str(source)
            ds.initialization_note = "Recycled spun-up state; not a chronological restart"
            if i == 0:
                ds["Times"][:] = np.asarray(list(stamp(date)), dtype="S1")[None, :]
                ds.START_DATE = stamp(date)
                for name in ACCUMULATIONS:
                    var = ds[name]
                    # Retain inactive-cell fill masks; only reset valid diagnostics.
                    var[:] = np.ma.asarray(var[:]) * 0
            else:
                ds.Restart_Time = stamp(date)
                ds.Since_Date = stamp(date)
                # Preserve warm-restart bookkeeping. Zero triggers the cold-start
                # output branch although the reader sets out_counts=1, skipping
                # the first hourly output. Do not alter model counter logic.
        # Verify all unmodified physical fields, tiled to bound memory.
        with nc.Dataset(source) as old, nc.Dataset(temporary) as new:
            if i == 1 and new.his_out_counts != old.his_out_counts:
                raise ValueError("Initialization changed the donor history counter")
            for name, var in old.variables.items():
                if name == "Times" or (i == 0 and name in ACCUMULATIONS):
                    continue
                var.set_auto_maskandscale(False)
                new[name].set_auto_maskandscale(False)
                for row in range(var.shape[0] if var.ndim else 1):
                    key = row if var.ndim else Ellipsis
                    if not np.array_equal(var[key], new[name][key], equal_nan=True):
                        raise ValueError(f"Initialization changed physical field {name}")
        temporary.replace(target)
    restart_check(targets, date)
    write_json(
        marker,
        {
            "start": date,
            "sources": sources,
            "reset_diagnostics": sorted(ACCUMULATIONS),
            "physical_fields_verified_unchanged": True,
            "history_counter_preserved": True,
        },
    )
    return targets


def namelist(path, settings):
    text = path.read_text()
    for key, value in settings.items():
        line = f"{key} = {value}"
        pattern = rf"(?im)^\s*{re.escape(key)}\s*=.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, line, text)
        else:
            text = text.replace("&HYDRO_nlist", "&HYDRO_nlist\n" + line, 1)
            if line not in text:
                raise ValueError(f"Missing land namelist key {key}")
    path.write_text(text)


def check_forcing(root, start, end):
    # Includes midnight beyond the last simulated day's calendar storage file.
    for day in dates(start, end + timedelta(days=1)):
        path = root / day.strftime("%Y/%m/%Y%m%d.LDASIN_DOMAIN1")
        with nc.Dataset(path) as ds:
            actual = times(ds)
            expected = [
                day + timedelta(hours=h)
                for h in range(24)
                if start < day + timedelta(hours=h) <= end
            ]
            if not set(expected).issubset(actual):
                raise ValueError(f"Missing required forcing hours in {path}")
            for name in ("T2D", "Q2D", "PSFC", "U2D", "V2D", "RAINRATE", "LWDOWN", "SWDOWN"):
                if name not in ds.variables:
                    raise ValueError(f"Missing forcing variable {name}: {path}")


def validate_outputs(work, start, end):
    hours = int((end - start).total_seconds() // 3600)
    hourly = sorted(work.glob("*.CHRTOUT_DOMAIN1"))
    expected = [start + timedelta(hours=h) for h in range(1, hours + 1)]
    actual = []
    for path in hourly:
        with nc.Dataset(path) as ds:
            actual.extend(times(ds))
            flow = np.ma.asarray(ds["streamflow"][:])
            if np.ma.count(flow) == 0 or not np.isfinite(flow.compressed()).all():
                raise ValueError(f"Invalid streamflow: {path}")
    if actual != expected:
        raise ValueError(
            f"Hourly output does not exactly cover (start,end]: {len(actual)} records; "
            f"missing={sorted(set(expected) - set(actual))}; "
            f"unexpected={sorted(set(actual) - set(expected))}"
        )
    daily = sorted(work.glob("*.LDASOUT_DOMAIN1.daily"))
    if [p.name[:8] for p in daily] != [d.strftime("%Y%m%d") for d in dates(start, end)]:
        raise ValueError("Daily land output coverage mismatch")
    for path, day in zip(daily, dates(start, end)):
        with nc.Dataset(path) as ds:
            bounds = ds["time_bounds"][:].reshape(-1)
            var = ds["time"]
            decoded = nc.num2date(bounds, var.units)
            if list(map(str, decoded)) != [str(day), str(day + timedelta(days=1))]:
                raise ValueError(f"Daily bounds mismatch: {path}")
            for name, var in ds.variables.items():
                if "time" not in var.dimensions or name in {"time", "time_bounds"}:
                    continue
                if "time:" not in getattr(var, "cell_methods", ""):
                    raise ValueError(f"Missing reduction metadata: {name}")
                values = np.ma.asarray(var[:])
                if not np.isfinite(values.compressed()).all():
                    raise ValueError(f"Nonfinite daily values: {name}")
    return hourly, daily


def output_inventory(work, logs):
    """Preserve timing evidence even when scratch is purged after a failed job."""
    records = []
    for path in sorted(work.iterdir()):
        if not any(tag in path.name for tag in ("CHRTOUT", "LDASOUT", "RESTART.", "HYDRO_RST.")):
            continue
        record = {"file": path.name, "bytes": path.stat().st_size}
        try:
            with nc.Dataset(path) as ds:
                if "time" in ds.variables:
                    record["times"] = times(ds)
                    record["time_units"] = ds["time"].units
                for name in ("Restart_Time", "Since_Date", "his_out_counts"):
                    if name in ds.ncattrs():
                        record[name] = str(ds.getncattr(name))
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError) as exc:
            record["read_error"] = str(exc)
        records.append(record)
    write_json(logs / "output-inventory.json", records)


def normalize_archive_time(dataset, expected):
    """Verify raw coordinates before replacing inherited per-run range metadata."""
    var = dataset["time"]
    calendar = getattr(var, "calendar", "standard")
    var.set_auto_mask(False)
    raw = np.asarray(var[:])
    wanted = nc.date2num(expected, var.units, calendar=calendar)
    if not expected or not np.array_equal(raw, wanted):
        raise ValueError("Archive raw timestamp readback failed")
    # NCO inherits attributes from the first input, whose model run may end
    # before later records. Do not let that range mask valid continuation data.
    if "valid_range" in var.ncattrs():
        var.delncattr("valid_range")
    var.valid_min = np.asarray(raw.min(), dtype=var.dtype)
    var.valid_max = np.asarray(raw.max(), dtype=var.dtype)
    var.set_auto_mask(True)
    if times(dataset) != expected:
        raise ValueError("Archive timestamp readback failed")
    dataset.time_coverage_start = expected[0].isoformat() + "Z"
    dataset.time_coverage_end = expected[-1].isoformat() + "Z"
    dataset.model_output_valid_time = "see time coordinate; multiple hourly records"
    dataset.model_total_valid_times = len(expected)


def publish_hourly(paths, destination, ncrcat):
    groups = {}
    for path in paths:
        groups.setdefault(path.name[:8], []).append(path)
    for day, inputs in groups.items():
        output = destination / day[:4] / day[4:6] / f"{day}.CHRTOUT_DOMAIN1"
        output.parent.mkdir(parents=True, exist_ok=True)
        # Existing data is only the preceding segment's midnight boundary record.
        expected = []
        if output.exists():
            with nc.Dataset(output) as ds:
                expected = times(ds)
            if len(expected) != 1 or expected[0].hour != 0:
                raise ValueError(f"Refusing overlap with existing archive: {output}")
            inputs = [output, *inputs]
        for path in inputs[int(output.exists()) :]:
            with nc.Dataset(path) as ds:
                expected.extend(times(ds))
        if expected != sorted(set(expected)) or len(expected) > 24:
            raise ValueError("Duplicate/unordered hourly publication")
        temporary = output.with_name(output.name + ".partial")
        subprocess.run(
            [str(ncrcat), "-O", "-4", "-L", "2", *map(str, inputs), str(temporary)], check=True
        )
        with nc.Dataset(temporary, "r+") as ds:
            normalize_archive_time(ds, expected)
            ds.storage_grouping = "calendar day 00-23 UTC"
            ds.temporal_resolution = "hourly"
            ds.calendar_day_complete = int(len(expected) == 24)
        temporary.replace(output)


def run_segment(project, work, logs, output, restart_root, inputs, start, end, ranks):
    hours = int((end - start).total_seconds() // 3600)
    domain = project / "nwm/static/operational/nwm.v3.1.6"
    forcing = project / "forcing/outputs/conus/retro"
    check_forcing(forcing, start, end)
    restart_check(inputs, start)
    work.mkdir(parents=True, exist_ok=False)
    logs.mkdir(parents=True, exist_ok=True)
    shutil.copy2(domain / "analysis_assim/namelist.hrldas", work)
    shutil.copy2(domain / "analysis_assim_no_lakes/hydro.namelist", work)
    (work / "DOMAIN").symlink_to(domain / "domain")
    for table in ("CHANPARM.TBL", "GENPARM.TBL", "HYDRO.TBL", "MPTABLE.TBL", "SOILPARM.TBL"):
        shutil.copy2(domain / "constants" / table, work)
    executable = (
        project / "external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe"
    )
    (work / "wrf_hydro.exe").symlink_to(executable)
    namelist(
        work / "namelist.hrldas",
        {
            "INDIR": repr(str(forcing)),
            "OUTDIR": "'./'",
            "START_YEAR": start.year,
            "START_MONTH": start.month,
            "START_DAY": start.day,
            "START_HOUR": 0,
            "START_MIN": 0,
            "KHOUR": hours,
            "FORC_TYP": 1,
            "PCP_PARTITION_OPTION": 1,
            "HRLDAS_SETUP_FILE": "'./DOMAIN/wrfinput_CONUS_NLDAS2.nc'",
            "RESTART_FILENAME_REQUESTED": repr(str(inputs[0])),
            "RESTART_FREQUENCY_HOURS": hours,
            "OUTPUT_TIMESTEP": 3600,
            "SPLIT_OUTPUT_COUNT": 1,
        },
    )
    namelist(
        work / "hydro.namelist",
        {
            "RESTART_FILE": repr(str(inputs[1])),
            "rst_dt": hours * 60,
            "rst_typ": 0,
            "RSTRT_SWC": 0,
            "GW_RESTART": 1,
            "t0OutputFlag": 0,
            "CHRTOUT_DOMAIN": 1,
            "CHRTOUT_HOURLY": 1,
            "CHRTOUT_DAILY": 0,
            "LDASOUT_HOURLY": 0,
            "LDASOUT_DAILY": 1,
            "LSMOUT_DOMAIN": 0,
            "CHANOBS_DOMAIN": 0,
            "CHRTOUT_GRID": 0,
            "RTOUT_DOMAIN": 0,
            "output_gw": 0,
            "outlake": 0,
            "frxst_pts_out": 0,
            "DTRT_TER": 600,
            "DTRT_CH": 600,
            "diversions_file": "''",
            "io_form_outputs": 3,
            "out_dt": 60,
            "SPLIT_OUTPUT_COUNT": 1,
        },
    )
    for name in ("namelist.hrldas", "hydro.namelist"):
        shutil.copy2(work / name, logs)
    try:
        with (logs / "model.log").open("w") as log:
            subprocess.run(
                ["mpiexec", "-n", str(ranks), "./wrf_hydro.exe"],
                cwd=work,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    finally:
        output_inventory(work, logs)
    if "The model finished successfully" not in (logs / "model.log").read_text():
        raise ValueError("Model completion sentinel missing")
    hourly, daily = validate_outputs(work, start, end)
    terminal = [work / name for name in restart_names(end)]
    restart_check(terminal, end)
    with nc.Dataset(domain / "domain/wrfinput_CONUS_NLDAS2.nc") as ds:
        active = np.asarray(ds["XLAND"][:]).squeeze() == 1
    check_active_state(terminal[0], active, ("SMC", "SH2O", "SOIL_T", "SNEQV"))
    for path in daily:
        check_active_state(path, active, ("SOIL_M", "SOIL_T", "SNEQV"))
    publish_hourly(hourly, output / "hourly", Path(os.sys.executable).parent / "ncrcat")
    for path in daily:
        copy_atomic(path, output / "daily" / path.name[:4] / path.name[4:6] / path.name)
    destinations = [restart_root / end.strftime("%Y/%m") / p.name for p in terminal]
    for src, dst in zip(terminal, destinations):
        copy_atomic(src, dst)
    restart_check(destinations, end)
    report = {
        "passed": True,
        "start": start,
        "end": end,
        "mpi_ranks": ranks,
        "hourly_records": len(hourly),
        "daily_records": len(daily),
        "restarts": destinations,
        "outputs": output,
        "scratch_output_bytes": sum(p.stat().st_size for p in [*hourly, *daily]),
    }
    write_json(logs / "accepted.json", report)
    # Only this run's validated scratch intermediates are removed.
    shutil.rmtree(work)
    return destinations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--year", type=int, default=1979)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.campaign):
        parser.error("Invalid campaign name")
    project = args.project.resolve()
    campaign = project / "nwm/runs/conus/retro" / args.campaign
    # flock prevents two controllers writing the same year's outputs.
    import fcntl

    campaign.mkdir(parents=True, exist_ok=True)
    with (campaign / f"{'test' if args.test else args.year}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gate = campaign / "test-passed.json"
        if not args.test and (
            not gate.exists() or not json.loads(gate.read_text()).get("continuation_passed")
        ):
            raise RuntimeError("Two-day and restart-continuation acceptance gate has not passed")
        start = datetime(args.year, 1, 2 if args.year == 1979 else 1)
        end = datetime(1979, 1, 4) if args.test else datetime(args.year + 1, 1, 1)
        if args.test and args.year != 1979:
            raise ValueError("Acceptance test is January 2-4, 1979")
        kind = "acceptance" if args.test else "production"
        output = project / "nwm/outputs/conus/retro" / args.campaign / kind
        restarts = project / "nwm/restarts/conus/retro" / args.campaign / kind
        inputs = (
            initialization(project, campaign / "initialization-preserved-counter")
            if args.year == 1979
            else [restarts / start.strftime("%Y/%m") / n for n in restart_names(start)]
        )
        scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
        segments = list(months(start, end))
        if args.test:
            segments.append((end, end + timedelta(days=1)))
        for begin, stop in segments:
            segment_label = begin.strftime("%Y%m%d") if args.test else begin.strftime("%Y%m")
            logs = campaign / kind / segment_label
            marker = logs / "accepted.json"
            if marker.exists():
                inputs = [Path(p) for p in json.loads(marker.read_text())["restarts"]]
                restart_check(inputs, stop)
                continue
            inputs = run_segment(
                project,
                scratch / f"{args.campaign}-{kind}-{segment_label}",
                logs,
                output,
                restarts,
                inputs,
                begin,
                stop,
                int(os.environ["SLURM_NTASKS"]),
            )
        write_json(
            gate if args.test else campaign / f"{args.year}-passed.json",
            {
                "passed": True,
                "start": start,
                "end": segments[-1][1],
                "restarts": inputs,
                "continuation_passed": args.test,
            },
        )


if __name__ == "__main__":
    main()
