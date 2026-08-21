"""Command-line interface for Hydro Ops."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import requests

from hydro_ops.config import load_settings
from hydro_ops.download.hrrr import HrrrDownloader
from hydro_ops.download.mrms import MrmsDownloader
from hydro_ops.download.nldas2 import Nldas2Downloader, iter_dates
from hydro_ops.download.prism import VARIABLES, PrismDownloader
from hydro_ops.download.stage4 import Stage4Downloader
from hydro_ops.download.stage4_convert import Stage4Converter

LOG = logging.getLogger(__name__)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid date {value!r}; use YYYY-MM-DD") from error


def date_range(args: argparse.Namespace, lag_days: int) -> tuple[date, date]:
    if args.date:
        if args.start or args.end:
            raise ValueError("Use --date or --start/--end, not both")
        return args.date, args.date
    if bool(args.start) != bool(args.end):
        raise ValueError("Use both --start and --end")
    if args.start:
        return args.start, args.end
    default = datetime.now(UTC).date() - timedelta(days=lag_days)
    return default, default


def add_dates(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", type=parse_date, help="one UTC date (YYYY-MM-DD)")
    parser.add_argument("--start", type=parse_date, help="first UTC date, inclusive")
    parser.add_argument("--end", type=parse_date, help="last UTC date, inclusive")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydro-ops")
    parser.add_argument("--verbose", action="store_true")
    actions = parser.add_subparsers(dest="action", required=True)
    download = actions.add_parser("download", help="download meteorological data")
    sources = download.add_subparsers(dest="source", required=True)
    nldas = sources.add_parser("nldas2", help="NLDAS-2 primary hourly forcing")
    add_dates(nldas)
    nldas.add_argument("--dry-run", action="store_true")
    stage4 = sources.add_parser("stage4", help="NOAA Stage-IV precipitation")
    add_dates(stage4)
    stage4.add_argument("--stream", choices=("realtime", "archive", "both"), default="both")
    stage4.add_argument("--dry-run", action="store_true")
    prism = sources.add_parser("prism", help="PRISM AN 4-km daily precipitation")
    add_dates(prism)
    prism.add_argument("--variable", dest="variables", action="append", choices=tuple(VARIABLES))
    prism.add_argument("--dry-run", action="store_true")
    hrrr = sources.add_parser("hrrr", help="HRRR CONUS hourly hydrologic forcing")
    add_dates(hrrr)
    hrrr.add_argument("--dry-run", action="store_true")
    mrms = sources.add_parser("mrms", help="MRMS CONUS hourly precipitation forcing")
    add_dates(mrms)
    mrms.add_argument("--dry-run", action="store_true")
    submit = actions.add_parser("submit", help="submit a download through SLURM")
    sources = submit.add_subparsers(dest="source", required=True)
    nldas = sources.add_parser("nldas2", help="submit NLDAS-2 download")
    add_dates(nldas)
    nldas.add_argument("--dry-run", action="store_true", help="print sbatch command")
    stage4 = sources.add_parser("stage4", help="submit Stage-IV download")
    add_dates(stage4)
    stage4.add_argument("--stream", choices=("realtime", "archive", "both"), default="both")
    stage4.add_argument("--dry-run", action="store_true", help="print sbatch command")
    prism = sources.add_parser("prism", help="submit PRISM precipitation download")
    add_dates(prism)
    prism.add_argument("--variable", dest="variables", action="append", choices=tuple(VARIABLES))
    prism.add_argument("--dry-run", action="store_true", help="print sbatch command")
    hrrr = sources.add_parser("hrrr", help="submit HRRR forcing download")
    add_dates(hrrr)
    hrrr.add_argument("--dry-run", action="store_true", help="print sbatch command")
    mrms = sources.add_parser("mrms", help="submit MRMS forcing download")
    add_dates(mrms)
    mrms.add_argument("--dry-run", action="store_true", help="print sbatch command")
    convert = actions.add_parser("convert", help="convert downloaded data")
    sources = convert.add_subparsers(dest="source", required=True)
    stage4 = sources.add_parser("stage4", help="convert local Stage-IV GRIB2 to NetCDF")
    stage4.add_argument("--stream", choices=("realtime", "archive", "both"), default="both")
    return parser


def download_nldas2(args: argparse.Namespace) -> int:
    settings = load_settings()
    start, end = date_range(args, settings.nldas_lag_days)
    downloader = Nldas2Downloader(settings, check_credentials=not args.dry_run)
    for day in iter_dates(start, end):
        downloader.download_day(day, dry_run=args.dry_run)
    return 0


def submit_nldas2(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        "--nodes=1",
        "--ntasks=1",
        f"--cpus-per-task={settings.nldas_download_jobs}",
        f"--time={settings.slurm_time}",
        "--job-name=nldas2_download",
        f"--output={settings.log_root}/nldas2-%j.out",
        f"--export=ALL,HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_PROJECT_ROOT={settings.project_root}",
    ]
    if settings.slurm_account:
        command.append(f"--account={settings.slurm_account}")
    command.append(str(settings.project_root / "slurm" / "download_nldas2.py"))
    for option in ("date", "start", "end"):
        value = getattr(args, option)
        if value:
            command.extend((f"--{option}", value.isoformat()))
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


def download_stage4(args: argparse.Namespace) -> int:
    settings = load_settings()
    downloader = Stage4Downloader(settings)
    streams = ("realtime", "archive") if args.stream == "both" else (args.stream,)
    for stream in streams:
        if args.date or args.start:
            start, end = date_range(args, 0)
        elif stream == "realtime":
            end = datetime.now(UTC).date()
            start = end - timedelta(days=settings.stage4_realtime_lookback_days)
        else:
            start = end = datetime.now(UTC).date() - timedelta(
                days=settings.stage4_archive_lag_days
            )
        for day in iter_dates(start, end):
            downloader.download_day(day, stream, dry_run=args.dry_run)
    return 0


def submit_stage4(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        "--nodes=1",
        "--ntasks=1",
        f"--cpus-per-task={settings.stage4_download_jobs}",
        f"--time={settings.slurm_time}",
        "--job-name=stage4_download",
        f"--output={settings.log_root}/stage4-%j.out",
        f"--export=ALL,HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_PROJECT_ROOT={settings.project_root}",
    ]
    if settings.slurm_account:
        command.append(f"--account={settings.slurm_account}")
    command.append(str(settings.project_root / "slurm" / "download_stage4.py"))
    command.extend(("--stream", args.stream))
    for option in ("date", "start", "end"):
        value = getattr(args, option)
        if value:
            command.extend((f"--{option}", value.isoformat()))
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


def prism_date_range(args: argparse.Namespace, settings) -> tuple[date, date]:
    if args.date or args.start:
        return date_range(args, 0)
    end = datetime.now(UTC).date() - timedelta(days=settings.prism_lag_days)
    return end - timedelta(days=settings.prism_refresh_days), end


def download_prism(args: argparse.Namespace) -> int:
    settings = load_settings()
    start, end = prism_date_range(args, settings)
    elements = tuple(args.variables) if args.variables else settings.prism_variables
    PrismDownloader(settings).download_range(start, end, elements, dry_run=args.dry_run)
    return 0


def submit_prism(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=1",
        f"--time={settings.slurm_time}",
        "--job-name=prism_download",
        f"--output={settings.log_root}/prism-%j.out",
        f"--export=ALL,HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_PROJECT_ROOT={settings.project_root}",
    ]
    if settings.slurm_account:
        command.append(f"--account={settings.slurm_account}")
    command.append(str(settings.project_root / "slurm" / "download_prism.py"))
    for option in ("date", "start", "end"):
        value = getattr(args, option)
        if value:
            command.extend((f"--{option}", value.isoformat()))
    for variable in args.variables or ():
        command.extend(("--variable", variable))
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


def download_hrrr(args: argparse.Namespace) -> int:
    settings = load_settings()
    start, end = date_range(args, settings.hrrr_lag_days)
    downloader = HrrrDownloader(settings)
    for day in iter_dates(start, end):
        downloader.download_day(day, dry_run=args.dry_run)
    return 0


def submit_hrrr(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        "--nodes=1",
        "--ntasks=1",
        f"--cpus-per-task={settings.hrrr_download_jobs}",
        f"--time={settings.slurm_time}",
        "--job-name=hrrr_download",
        f"--output={settings.log_root}/hrrr-%j.out",
        f"--export=ALL,HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_PROJECT_ROOT={settings.project_root}",
    ]
    if settings.slurm_account:
        command.append(f"--account={settings.slurm_account}")
    command.append(str(settings.project_root / "slurm" / "download_hrrr.py"))
    for option in ("date", "start", "end"):
        value = getattr(args, option)
        if value:
            command.extend((f"--{option}", value.isoformat()))
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


def download_mrms(args: argparse.Namespace) -> int:
    settings = load_settings()
    downloader = MrmsDownloader(settings)
    if args.date or args.start:
        start, end = date_range(args, 0)
        latest = None
        allow_missing = False
    else:
        now = datetime.now(UTC)
        latest = (now - timedelta(minutes=20)).replace(minute=0, second=0, microsecond=0)
        end = now.date()
        start = end - timedelta(days=settings.mrms_realtime_lookback_days)
        allow_missing = True
    for day in iter_dates(start, end):
        downloader.download_day(
            day,
            latest=latest,
            allow_missing=allow_missing,
            dry_run=args.dry_run,
        )
    return 0


def submit_mrms(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.log_root.mkdir(parents=True, exist_ok=True)
    command = [
        "sbatch",
        f"--partition={settings.slurm_partition}",
        "--nodes=1",
        "--ntasks=1",
        f"--cpus-per-task={settings.mrms_download_jobs}",
        f"--time={settings.slurm_time}",
        "--job-name=mrms_download",
        f"--output={settings.log_root}/mrms-%j.out",
        f"--export=ALL,HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_PROJECT_ROOT={settings.project_root}",
    ]
    if settings.slurm_account:
        command.append(f"--account={settings.slurm_account}")
    command.append(str(settings.project_root / "slurm" / "download_mrms.py"))
    for option in ("date", "start", "end"):
        value = getattr(args, option)
        if value:
            command.extend((f"--{option}", value.isoformat()))
    if args.dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command, check=False).returncode


def convert_stage4(args: argparse.Namespace) -> int:
    converter = Stage4Converter(load_settings())
    streams = ("realtime", "archive") if args.stream == "both" else (args.stream,)
    for stream in streams:
        converter.convert_existing(stream)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        if args.action == "convert":
            return convert_stage4(args)
        if args.source == "nldas2":
            return download_nldas2(args) if args.action == "download" else submit_nldas2(args)
        if args.source == "prism":
            return download_prism(args) if args.action == "download" else submit_prism(args)
        if args.source == "hrrr":
            return download_hrrr(args) if args.action == "download" else submit_hrrr(args)
        if args.source == "mrms":
            return download_mrms(args) if args.action == "download" else submit_mrms(args)
        return download_stage4(args) if args.action == "download" else submit_stage4(args)
    except (OSError, RuntimeError, ValueError, requests.RequestException) as error:
        LOG.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
