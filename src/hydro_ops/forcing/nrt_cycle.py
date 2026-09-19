"""Source-aware, isolated recent-NRT updates; never used by retrospective repairs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.download.gfs import GfsDownloader
from hydro_ops.forcing.complete_day import produce_complete_day, utc_hours
from hydro_ops.forcing.daily_archive import create_daily_archive
from hydro_ops.forcing.gfs_publication import _atomic_json, publish_gfs_day
from hydro_ops.forcing.native_donor import FIELDS, NativeDonorRepair
from hydro_ops.forcing.operational_strategy import latency
from hydro_ops.forcing.operations import (
    OperationalLayout,
    discover_precipitation_candidates,
    discover_stage4_six_hour,
)
from hydro_ops.forcing.source_selection import select_hourly_source
from hydro_ops.forcing.window_cache import WindowCache

POLICY = "source_aware_recent_nrt_v1"


def configuration(root):
    path = root / "config/nrt_gfs.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {"enabled": False}


def baseline_configuration(config):
    """Lossless writer tuning must not invalidate accepted native baselines."""
    result = {k: v for k, v in config.items()
            if k not in {"reconciliation_writer_profile", "baseline_writer_profile",
                         "precipitation_remap_workers", "native_repair_workers", "gfs_sparse_writes",
                         "window_cache_enabled", "window_cache_max_entries", "window_cache_max_bytes", "window_cache_max_age_days"}}
    # Preserve the historical fingerprint value: worker count is not scientific input.
    if "assembly_workers" in result:
        result["assembly_workers"] = 4
    return result


def baseline_archive_options(config):
    profile = config.get("baseline_writer_profile", "reference")
    if profile not in {"reference", "validated_source_chunks_v1"}:
        raise ValueError(f"Unknown NRT baseline writer profile: {profile}")
    enabled = profile == "validated_source_chunks_v1"
    return {"chunk_copy": enabled, "preserve_source_chunks": enabled}


def baseline_workers(config):
    """Benchmark overrides only; production retains its accepted worker counts."""
    result = {"assembly_workers": int(os.environ.get("HYDRO_OPS_NRT_ASSEMBLY_WORKERS", config["assembly_workers"])),
              "precipitation_remap_workers": int(os.environ.get("HYDRO_OPS_NRT_PRECIP_WORKERS", config.get("precipitation_remap_workers", 1)))}
    if any(value < 1 or value > 16 for value in result.values()):
        raise ValueError("NRT baseline worker counts must be between 1 and 16")
    return result


def baseline_repair_options(config):
    workers = int(os.environ.get('HYDRO_OPS_NRT_REPAIR_WORKERS', config.get('native_repair_workers', 1)))
    sparse = os.environ.get('HYDRO_OPS_NRT_GFS_SPARSE_WRITES', '1' if config.get('gfs_sparse_writes', False) else '0')
    if not 1 <= workers <= 8 or sparse not in {'0', '1'}:
        raise ValueError('Invalid native repair worker count or GFS writer override')
    return workers, sparse == '1'


def _initialize_repair(layout, envelope, terrain, maximum_km, geometry):
    global _worker_repair, _worker_gap
    _worker_repair = NativeDonorRepair(layout, envelope, terrain, maximum_km=maximum_km)
    with np.load(geometry) as data:
        _worker_gap = np.zeros(tuple(data['shape']), dtype=bool)
        _worker_gap.ravel()[data['indices']] = True


def _repair_hour(task):
    path, selection = task
    return _worker_repair.repair(path, selection,
        deferred_mask=_worker_gap if selection.product == 'hrrr' else None)


def reconciliation_environment(config, environ):
    profile = config.get("reconciliation_writer_profile", "reference")
    if profile not in {"reference", "validated_chunks_reuse_v1"}:
        raise ValueError(f"Unknown NRT reconciliation writer profile: {profile}")
    env = dict(environ)
    enabled = "1" if profile == "validated_chunks_reuse_v1" else "0"
    for key in ("HYDRO_OPS_ARCHIVE_CHUNKS", "HYDRO_OPS_NRT_REUSE_WINDOWS"):
        env.setdefault(key, enabled)  # Explicit benchmark/reference overrides win.
        if env[key] not in {"0", "1"}:
            raise ValueError(f"Invalid writer override: {key}={env[key]}")
    env.setdefault("HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS", env["HYDRO_OPS_ARCHIVE_CHUNKS"])
    if env["HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS"] not in {"0", "1"}:
        raise ValueError("Invalid source-chunk preservation override")
    return env


def activation(root):
    config = configuration(root)
    receipt = read_json(root / config.get("activation_receipt", "forcing/status/nrt-gfs/activation.json"))
    return bool(config.get("enabled") and receipt.get("status") == "passed" and receipt.get("policy") == POLICY)


def require_operational_gfs(root):
    """Fail closed; an unaccepted/disabled fallback is not a legacy-mode switch."""
    if not activation(root):
        raise RuntimeError("NRT requires validated GFS northern fallback: enable config/nrt_gfs.toml "
                           "and restore a passed activation receipt. Legacy NRT fallback is forbidden.")


def identity(path):
    path = Path(path)
    if not path.is_file():
        return {"path": str(path.resolve()), "missing": True}
    stat = path.stat()
    return {"path": str(path.resolve()), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def window_signature(day, baselines, prism, revision, chunks):
    required = {str(day - timedelta(days=1)), str(day)}
    records = {r["day"]: r["sha256"] for _, r in baselines if r["day"] in required}
    if set(records) != required:
        raise ValueError("Incomplete PRISM-window baseline dependencies")
    return fingerprint({"baselines": records,
                        "prism": [identity(p) for p in prism if f"{day:%Y%m%d}" in p.name],
                        "revision": revision, "chunks": chunks})


def source_runs(selections):
    """Contiguous runs retain batch remapping, including a mid-day source change."""
    first = 0
    for index in range(1, len(selections) + 1):
        if index == len(selections) or selections[index].product != selections[first].product:
            yield first, index - 1
            first = index


def baseline_plan(day, layout, config):
    selections = [select_hourly_source(t, layout.nldas2_root, layout.hrrr_root) for t in utc_hours(day)]
    paths = {s.path for s in selections}
    start = utc_hours(day)[0] - timedelta(hours=5)
    for index in range(30):
        candidates, quality = discover_precipitation_candidates(start + timedelta(hours=index), layout)
        paths.update(candidates.values())
        if quality:
            paths.add(quality)
        six = discover_stage4_six_hour(start + timedelta(hours=index), layout)
        if six:
            paths.add(six)
    record = {"policy": POLICY, "day": str(day), "configuration": baseline_configuration(config),
              "hourly_primary_sources": [s.product for s in selections],
              "files": [identity(p) for p in sorted(paths)]}
    return selections, record


def up_to_date(path, receipt, expected):
    try:
        return (receipt["status"] == "passed" and receipt["input_fingerprint"] == expected
                and receipt["published_identity"] == identity(path)
                and not receipt.get("retry_preferred_gfs_cycle", False))
    except KeyError:
        return False


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def day_path(root, day):
    return root / f"{day:%Y/%m/%Y%m%d}.LDASIN_DOMAIN1"


def replacement_backlog(output, layout, before):
    """Keep old fallback days discoverable even after they age out of the lookback."""
    ready = []
    for path in output.glob("*/*/*.nrt-receipt.json"):
        record = read_json(path)
        if not record.get("gfs_hours"):
            continue
        day = date.fromisoformat(record["day"])
        if day >= before:
            continue
        for hour, old in enumerate(record.get("hourly_primary_sources", [])):
            if old == "hrrr":
                try:
                    selection = select_hourly_source(utc_hours(day)[hour], layout.nldas2_root, layout.hrrr_root)
                except FileNotFoundError:
                    continue
                if selection.product == "nldas2":
                    ready.append(day)
                    break
    return sorted(set(ready))


def transfer(source, destination):
    """Check a complete private candidate before replacing a permanent file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".nrt.part")
    shutil.copyfile(source, partial)
    with source.open("rb") as a, partial.open("rb") as b:
        checksum = hashlib.file_digest(a, "sha256").hexdigest()
        if hashlib.file_digest(b, "sha256").hexdigest() != checksum:
            raise ValueError("NRT publication transfer checksum differs")
    os.replace(partial, destination)
    return checksum


class RecentNrt:
    def __init__(self, root, work, as_of, *, output_root=None, baseline_root=None):
        if as_of.tzinfo is None:
            raise ValueError("NRT source selection requires an aware as-of time")
        self.root, self.work, self.as_of = root.resolve(), work, as_of
        self.config = configuration(root)
        self.archive_options = baseline_archive_options(self.config)
        self.layout = OperationalLayout.project_defaults(root)
        self.output = output_root or root / "forcing/outputs/conus/nrt"
        self.baseline = baseline_root or root / "forcing/outputs/conus/baseline"
        self.envelope = root / self.config["envelope"]
        self.geometry = root / self.config["geometry"]
        self.conservative = root / self.config["conservative_weights"]
        self.cache = root / self.config["cache"]
        self.work.mkdir(parents=True, exist_ok=True)
        with np.load(self.geometry) as data:
            self.gap = np.zeros(tuple(data["shape"]), dtype=bool)
            self.gap.ravel()[data["indices"]] = True
        self.repair = NativeDonorRepair(self.layout, self.envelope,
            root / self.config["model_terrain"], maximum_km=self.config["maximum_native_donor_km"])
        # Changes to fixed assets must invalidate old acceptance receipts too.
        self.assets = [identity(p) for p in (self.envelope, self.geometry, self.conservative,
                      self.layout.target_grid, self.layout.remap_grid, self.layout.target_elevation,
                      self.layout.nldas2_elevation, self.layout.hrrr_elevation,
                      root / self.config["model_terrain"], self.layout.nldas2_bilinear,
                      self.layout.hrrr_bilinear, self.layout.nldas2_conservative,
                      self.layout.hrrr_conservative, self.layout.mrms_conservative,
                      self.layout.mrms_quality_bilinear, self.layout.stage4_conservative,
                      self.layout.cnrfc_nwm_mask)]

    def baseline_day(self, day):
        started = time.monotonic()
        selections, inputs = baseline_plan(day, self.layout, self.config)
        inputs["assets"] = self.assets
        downloader = GfsDownloader(self.cache, self.work)
        bundles = []
        for selection in selections:
            if selection.product == "hrrr":
                bundle = downloader.hour(selection.valid_time, as_of=self.as_of)
                bundles.append({k: bundle.attrs.get(k) for k in
                                ("valid_time", "cycle", "lead", "url", "remote_etag", "remote_last_modified")})
        inputs["gfs_bundles"] = bundles
        timings = [{"stage": "source_planning_and_gfs_acquisition", "seconds": time.monotonic() - started}]
        expected = fingerprint(inputs)
        destination = day_path(self.baseline, day)
        receipt_path = destination.with_name(destination.name + ".nrt-receipt.json")
        receipt = read_json(receipt_path)
        if up_to_date(destination, receipt, expected):
            return destination, receipt
        old_modes = receipt.get("hourly_primary_sources", [])
        if len(old_modes) == 24 and any(old == "nldas2" and new.product != "nldas2"
                                       for old, new in zip(old_modes, selections, strict=True)):
            raise RuntimeError("Refusing to downgrade a retained NLDAS-2 baseline during a source outage")
        with tempfile.TemporaryDirectory(prefix=f"nrt-baseline-{day:%Y%m%d}-", dir=self.work) as tmp:
            tmp = Path(tmp)
            hourly = tmp / "hourly"
            workers = baseline_workers(self.config)
            started = time.monotonic()
            for first, last in source_runs(selections):
                produce_complete_day(day, self.layout, hourly, work_directory=tmp,
                    start_hour=first, end_hour=last, **workers)
            timings.append({"stage": "complete_day", "seconds": time.monotonic() - started, **workers})
            paths = [hourly / s.valid_time.strftime("%Y/%m/%d/%Y%m%d%H.LDASIN_DOMAIN1") for s in selections]
            started = time.monotonic()
            repair_workers, sparse_writes = baseline_repair_options(self.config)
            if repair_workers == 1:
                repairs = [self.repair.repair(path, s, deferred_mask=self.gap if s.product == 'hrrr' else None)
                           for path, s in zip(paths, selections, strict=True)]
            else:
                with ProcessPoolExecutor(max_workers=repair_workers, mp_context=multiprocessing.get_context('spawn'),
                    initializer=_initialize_repair, initargs=(self.layout, self.envelope,
                        self.root / self.config['model_terrain'], self.config['maximum_native_donor_km'], self.geometry)) as pool:
                    repairs = list(pool.map(_repair_hour, zip(paths, selections, strict=True)))
            timings.append({"stage": "native_donor_repair", "seconds": time.monotonic() - started, "workers": repair_workers})
            assembled, corrected = tmp / "assembled.nc", tmp / "corrected.nc"
            started = time.monotonic()
            create_daily_archive(paths, assembled, day, work_directory=tmp, **self.archive_options)
            timings.append({"stage": "daily_archive", "seconds": time.monotonic() - started})
            archive_record = json.loads(assembled.with_name(assembled.name + ".manifest.json").read_text())
            started = time.monotonic()
            report = publish_gfs_day(assembled, corrected, self.envelope, self.geometry,
                self.conservative, self.cache, tmp, nldas_available=False, as_of=self.as_of,
                allow_mixed=True, require_native_repair=True,
                sparse_writes=sparse_writes)
            timings.append({"stage": "gfs_publication_and_audit", "seconds": time.monotonic() - started,
                            "sparse_writes": sparse_writes})
            if report["status"] != "passed":
                raise ValueError(f"NRT baseline not accepted: {report}")
            _, current = baseline_plan(day, self.layout, self.config)
            current["assets"] = self.assets
            current["gfs_bundles"] = bundles
            if fingerprint(current) != expected:
                raise ValueError("Native inputs changed during build; retain previous publication")
            with Dataset(corrected, "r+") as data:
                data.forcing_stream = "baseline"
                data.archive_granularity = "utc_calendar_day"
                data.nrt_production_policy = POLICY
                data.gfs_publication_status = "operational_nrt_baseline"
            started = time.monotonic()
            checksum = transfer(corrected, destination)
            timings.append({"stage": "baseline_transfer", "seconds": time.monotonic() - started})
        receipt = {"status": "passed", "day": str(day), "input_fingerprint": expected,
                   "baseline_writer_profile": self.config.get("baseline_writer_profile", "reference"),
                   "baseline_archive_writer": archive_record.get("archive_writer", "value_based"),
                   "baseline_archive_timing": archive_record.get("timing"),
                   "baseline_stage_timings": timings,
                   "inputs": inputs, "published_identity": identity(destination), "sha256": checksum,
                   "as_of": self.as_of.isoformat(), "gfs_hours": report["gfs_hours"],
                   "hourly_primary_sources": inputs["hourly_primary_sources"], "gfs_hourly": report["hours"],
                   "retry_preferred_gfs_cycle": any(h["lead"] > 6 for h in report["hours"]),
                   "native_repairs": repairs}
        _atomic_json(receipt_path, receipt)
        _atomic_json(destination.with_name(destination.name + ".manifest.json"), {
            "daily_file": str(destination), "verified": True, "forcing_stream": "baseline",
            "verification": POLICY, "nrt_receipt": str(receipt_path), "source_files": inputs["files"]})
        return destination, receipt

    def prism_paths(self, day):
        root = self.root / "forcing/inputs/oregon_state/prism/an/4km/daily"
        return [root / variable / f"{d:%Y/%m}/prism_{variable}_us_25m_{d:%Y%m%d}.nc"
                for d in (day, day + timedelta(days=1)) for variable in ("ppt", "tmin", "tmax")]

    def produce_day(self, day):
        prism = self.prism_paths(day)
        constrained = all(p.is_file() for p in prism)
        required = [day + timedelta(days=i) for i in (-1, 0, 1)] if constrained else [day]
        baselines = [self.baseline_day(d) for d in required]
        inputs = {"baseline_fingerprints": [r["input_fingerprint"] for _, r in baselines],
                  "baseline_sha256": [r["sha256"] for _, r in baselines],
                  "prism": [identity(p) for p in prism], "policy": POLICY}
        expected = fingerprint(inputs)
        destination = day_path(self.output, day)
        receipt_path = destination.with_name(destination.name + ".nrt-receipt.json")
        old = read_json(receipt_path)
        if old.get("prism_constrained") and not constrained:
            raise RuntimeError("Retain existing PRISM-constrained NRT while its inputs are missing")
        if up_to_date(destination, old, expected):
            return {"day": str(day), "status": "unchanged", "gfs_hours": old["gfs_hours"],
                    "prism_constrained": old["prism_constrained"]}
        with tempfile.TemporaryDirectory(prefix="nrt-final-", dir=self.work) as tmp:
            tmp = Path(tmp)
            writer_env = reconciliation_environment(self.config, os.environ)
            window_writers = []
            timings = []
            def run(*args):
                started = time.monotonic()
                subprocess.run([sys.executable, *map(str, args)], cwd=self.root, env=writer_env, check=True)
                timings.append({"stage": Path(args[0]).name, "seconds": time.monotonic() - started})
            if constrained:
                # Share only dependency-keyed PRISM windows
                # within this worker's scratch, never across production workers.
                reuse = writer_env["HYDRO_OPS_NRT_REUSE_WINDOWS"] == "1"
                windows = self.work / "nrt-prism-windows/nrt" if reuse else tmp / "windows/nrt"
                # Keep private/test streams isolated by default; empty override disables caching.
                default_cache = str(self.output / '.prism-window-cache') if self.config.get("window_cache_enabled", False) else ""
                cache_root = os.environ.get("HYDRO_OPS_NRT_WINDOW_CACHE", default_cache)
                persistent = WindowCache(cache_root) if cache_root and reuse else None
                for d in (day, day + timedelta(days=1)):
                    signature = window_signature(d, baselines, prism,
                        "early" if (self.as_of.date() - d).days < 30 else "provisional",
                        writer_env["HYDRO_OPS_ARCHIVE_CHUNKS"] + ":" + writer_env["HYDRO_OPS_ARCHIVE_PRESERVE_SOURCE_CHUNKS"])
                    # Include code and all fixed remapping assets, not just weather inputs.
                    key = fingerprint({"window": signature, "assets": self.assets,
                        "prism_assets": [identity(self.root / p) for p in
                            ("forcing/static/prism/prism_an_4km_elevation.nc",
                             "forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc",
                             "forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc")],
                        "library": [identity(p) for p in sorted((self.root / "src/hydro_ops/forcing").glob("*.py"))],
                        "code": [identity(self.root / p) for p in
                            ("bin/produce_prism_constrained_daily.py", "bin/reconcile_prism_precipitation_day.py",
                             "src/hydro_ops/forcing/prism_temperature.py", "src/hydro_ops/forcing/precipitation_reconciliation.py",
                             "src/hydro_ops/forcing/nrt_cycle.py")]})
                    window = day_path(windows, d)
                    marker = window.with_name(window.name + ".reuse.json")
                    cached = read_json(marker) if reuse else {}
                    if cached.get("signature") == signature and cached.get("identity") == identity(window):
                        window_writers.append(read_json(window.with_name(window.name + ".manifest.json")).get("archive_writer", "value_based"))
                        print(json.dumps({"stage": "reuse_prism_window", "day": str(d)}), flush=True)
                        continue
                    started = time.monotonic()
                    if persistent and persistent.restore(key, window, identity):
                        timings.append({"stage": "persistent_window_hit", "day": str(d), "seconds": time.monotonic() - started})
                        window_writers.append(read_json(window.with_name(window.name + ".manifest.json")).get("archive_writer", "value_based"))
                        _atomic_json(marker, {"signature": signature, "identity": identity(window)})
                        continue
                    run(self.root / "bin/produce_prism_constrained_daily.py", "--day", d,
                        "--complete-root", self.baseline, "--output-root", windows,
                        "--revision", "early" if (self.as_of.date() - d).days < 30 else "provisional",
                        "--stream", "nrt", "--work-directory", tmp,
                        "--archive-access", "direct", "--allow-legacy-12utc-output", "--force")
                    window_writers.append(read_json(window.with_name(window.name + ".manifest.json")).get("archive_writer", "value_based"))
                    if reuse:
                        _atomic_json(marker, {"signature": signature, "identity": identity(window)})
                    if persistent:
                        started = time.monotonic()
                        persistent.publish(key, window, identity)
                        timings.append({"stage": "persistent_window_store", "day": str(d), "seconds": time.monotonic() - started})
                if persistent:
                    removed = persistent.prune(max_entries=self.config.get("window_cache_max_entries", 32),
                        max_bytes=self.config.get("window_cache_max_bytes", 160_000_000_000),
                        max_age_days=self.config.get("window_cache_max_age_days", 14))
                    timings.append({"stage": "persistent_window_prune", "removed_entries": len(removed)})
                run(self.root / "bin/materialize_calendar_forcing.py", "--input-root", windows,
                    "--output-root", tmp / "final", "--start", day, "--days", 1, "--stream", "nrt",
                    "--require-accepted-prism-windows", "--hierarchical", "--work-directory", tmp)
                candidate = day_path(tmp / "final", day)
            else:
                candidate = tmp / "unconstrained.nc"
                shutil.copyfile(baselines[0][0], candidate)
            started = time.monotonic()
            self.audit_final(candidate, day, constrained)
            timings.append({"stage": "audit_final", "seconds": time.monotonic() - started})
            calendar_record = read_json(candidate.with_name(candidate.name + ".manifest.json"))
            if [identity(p) for p in prism] != inputs["prism"]:
                raise ValueError("PRISM changed during reconciliation; retain previous publication")
            started = time.monotonic()
            checksum = transfer(candidate, destination)
            timings.append({"stage": "final_transfer", "seconds": time.monotonic() - started})
        receipt = {"status": "passed", "day": str(day), "input_fingerprint": expected,
                   "inputs": inputs, "published_identity": identity(destination), "sha256": checksum,
                   "calendar_archive_writer": calendar_record.get("archive_writer", "value_based") if constrained else None,
                   "prism_window_archive_writers": window_writers,
                   "stage_timings": timings,
                   "as_of": self.as_of.isoformat(), "prism_constrained": constrained,
                   "gfs_hours": next(r["gfs_hours"] for p, r in baselines if r["day"] == str(day)),
                   "hourly_primary_sources": next(r["hourly_primary_sources"] for p, r in baselines if r["day"] == str(day))}
        _atomic_json(receipt_path, receipt)
        _atomic_json(destination.with_name(destination.name + ".manifest.json"), {
            "daily_file": str(destination), "verified": True, "forcing_stream": "nrt",
            "verification": POLICY, "nrt_receipt": str(receipt_path), **receipt})
        return {"day": str(day), "status": "published", "gfs_hours": receipt["gfs_hours"],
                "prism_constrained": constrained}

    def audit_final(self, path, day, constrained):
        with Dataset(path, "r+") as data:
            times = num2date(data["time"][:], data["time"].units, only_use_cftime_datetimes=False)
            if [(t.date(), t.hour, t.minute, t.second) for t in times] != [(day, h, 0, 0) for h in range(24)]:
                raise ValueError("NRT output is not 00–23 UTC")
            if not getattr(data, "cnrfc_stage4_policy", ""):
                raise ValueError("NRT output lost CNRFC exclusion provenance")
            if constrained and str(getattr(data, "prism_reconciliation_accepted", "false")).lower() != "true":
                raise ValueError("NRT output lacks accepted PRISM reconciliation")
            for index in range(24):
                for name in FIELDS:
                    values = np.ma.filled(data[name][index], np.nan)
                    if not np.isfinite(values[self.repair.active]).all():
                        raise ValueError(f"Final NRT active holes: {index} {name}")
                    values[~self.repair.keep] = np.nan
                    data[name][index] = np.where(np.isfinite(values), values, data[name]._FillValue)
            data.forcing_stream = "nrt"
            data.archive_granularity = "utc_calendar_day"
            data.nrt_production_policy = POLICY
            data.prism_constraint_status = "applied" if constrained else "awaiting_complete_daily_inputs"
            if not constrained:
                data.prism_reconciliation_accepted = "false"
            data.forcing_domain_policy = "nrt_native_gfs_static_envelope_v1"
        with Dataset(path) as data:
            for index in range(24):
                for name in FIELDS:
                    values = np.ma.filled(data[name][index], np.nan)
                    if not np.isfinite(values[self.repair.active]).all() or np.isfinite(values[~self.repair.keep]).any():
                        raise ValueError(f"NRT readback failed: {index} {name}")


def run_cycle(root, work, start, end, as_of, *, output_root=None, baseline_root=None,
              requested_at=None, state_root=None):
    """An exclusive recent-NRT writer; failures retain the previous daily files."""
    worker_started = datetime.now(UTC)
    state_root = state_root or root / "forcing/status/nrt-gfs"
    state_root.mkdir(parents=True, exist_ok=True)
    report = {"policy": POLICY, "start": str(start), "end": str(end), "as_of": as_of.isoformat(),
              "status": "running", "job_id": os.environ.get("SLURM_JOB_ID"), "days": [], "errors": []}
    report["worker_started_utc"] = worker_started.isoformat()
    report["requested_at"] = requested_at
    with (state_root / "cycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _atomic_json(state_root / "latest.json", report)
        try:
            engine = RecentNrt(root, work, as_of, output_root=output_root, baseline_root=baseline_root)
        except (OSError, ValueError, RuntimeError, AssertionError) as error:
            report.update(status="failed", errors=[{"stage": "initialization", "error": str(error)}])
            _atomic_json(state_root / "latest.json", report)
            return report
        backlog = replacement_backlog(engine.output, engine.layout, start)
        report["replacement_backlog_days"] = [str(d) for d in backlog]
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        days += backlog[:engine.config.get("maximum_old_replacement_days", 2)]
        for day in days:
            day_started = time.monotonic()
            try:
                result = engine.produce_day(day)
                result["worker_seconds"] = time.monotonic() - day_started
                report["days"].append(result)
            except (OSError, ValueError, RuntimeError, AssertionError, subprocess.CalledProcessError) as error:
                report["errors"].append({"day": str(day), "error": str(error),
                                         "worker_seconds": time.monotonic() - day_started})
            _atomic_json(state_root / "latest.json", report)
        report["status"] = "failed" if report["errors"] else "passed"
        report["finished_utc"] = datetime.now(UTC).isoformat()
        report["latency"] = (latency(requested_at, worker_started, report["finished_utc"])
                             if requested_at else {"latency_status": "launch_time_unknown"})
        _atomic_json(state_root / "latest.json", report)
        _atomic_json(state_root / f"cycle-{os.environ.get('SLURM_JOB_ID', as_of.strftime('%Y%m%dT%H%M%S'))}.json", report)
    return report
