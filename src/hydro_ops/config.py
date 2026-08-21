"""Project configuration loading and environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def find_project_root(start: Path | None = None) -> Path:
    """Find the nearest directory containing config/project.toml."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config" / "project.toml").is_file():
            return candidate
    raise RuntimeError(
        "Could not find config/project.toml. Run from inside the hydro-ops project "
        "or set HYDRO_OPS_PROJECT_ROOT."
    )


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    output_root: Path
    work_root: Path
    log_root: Path
    slurm_partition: str
    slurm_account: str
    slurm_time: str
    nldas_base_url: str
    nldas_data_dir: Path
    nldas_netrc: Path
    nldas_cookies: Path
    nldas_download_jobs: int
    nldas_lag_days: int
    nldas_retries: int
    nldas_connect_timeout: int
    nldas_read_timeout: int
    stage4_realtime_base_url: str
    stage4_archive_base_url: str
    stage4_data_dir: Path
    stage4_download_jobs: int
    stage4_realtime_lookback_days: int
    stage4_archive_lag_days: int
    stage4_retries: int
    stage4_connect_timeout: int
    stage4_read_timeout: int
    stage4_wgrib2: str
    prism_base_url: str
    prism_data_dir: Path
    prism_variables: tuple[str, ...]
    prism_refresh_days: int
    prism_lag_days: int
    prism_retries: int
    prism_connect_timeout: int
    prism_read_timeout: int
    prism_request_delay: float
    hrrr_base_url: str
    hrrr_data_dir: Path
    hrrr_download_jobs: int
    hrrr_lag_days: int
    hrrr_retries: int
    hrrr_connect_timeout: int
    hrrr_read_timeout: int
    hrrr_wgrib2: str
    mrms_base_url: str
    mrms_data_dir: Path
    mrms_products: tuple[str, ...]
    mrms_download_jobs: int
    mrms_realtime_lookback_days: int
    mrms_retries: int
    mrms_connect_timeout: int
    mrms_read_timeout: int
    mrms_wgrib2: str


def load_settings() -> Settings:
    """Load project.toml, optional local.toml, then HYDRO_OPS_* overrides."""
    root_override = os.getenv("HYDRO_OPS_PROJECT_ROOT")
    root = find_project_root(Path(root_override)) if root_override else find_project_root()
    with (root / "config" / "project.toml").open("rb") as stream:
        values = tomllib.load(stream)
    local = root / "config" / "local.toml"
    if local.is_file():
        with local.open("rb") as stream:
            _merge(values, tomllib.load(stream))

    paths = values["paths"]
    slurm = values["slurm"]
    nldas = values["nldas2"]
    stage4 = values["stage4"]
    prism = values["prism"]
    hrrr = values["hrrr"]
    mrms = values["mrms"]

    def env(name: str, default: Any) -> Any:
        return os.getenv(f"HYDRO_OPS_{name}", default)

    return Settings(
        project_root=root,
        data_root=_path(root, env("DATA_ROOT", paths["data_root"])),
        output_root=_path(root, env("OUTPUT_ROOT", paths["output_root"])),
        work_root=_path(root, env("WORK_ROOT", paths["work_root"])),
        log_root=_path(root, env("LOG_ROOT", paths["log_root"])),
        slurm_partition=str(env("SLURM_PARTITION", slurm["partition"])),
        slurm_account=str(env("SLURM_ACCOUNT", slurm["account"])),
        slurm_time=str(env("SLURM_TIME", slurm["time"])),
        nldas_base_url=str(env("NLDAS_BASE_URL", nldas["base_url"])).rstrip("/"),
        nldas_data_dir=_path(root, env("NLDAS_DATA_DIR", nldas["data_dir"])),
        nldas_netrc=_path(root, env("NLDAS_EARTHDATA_NETRC", nldas["netrc"])),
        nldas_cookies=_path(root, env("NLDAS_EARTHDATA_COOKIES", nldas["cookies"])),
        nldas_download_jobs=int(env("NLDAS_DOWNLOAD_JOBS", nldas["download_jobs"])),
        nldas_lag_days=int(env("NLDAS_LAG_DAYS", nldas["lag_days"])),
        nldas_retries=int(env("NLDAS_RETRIES", nldas["retries"])),
        nldas_connect_timeout=int(
            env("NLDAS_CONNECT_TIMEOUT_SECONDS", nldas["connect_timeout_seconds"])
        ),
        nldas_read_timeout=int(env("NLDAS_READ_TIMEOUT_SECONDS", nldas["read_timeout_seconds"])),
        stage4_realtime_base_url=str(
            env("STAGE4_REALTIME_BASE_URL", stage4["realtime_base_url"])
        ).rstrip("/"),
        stage4_archive_base_url=str(
            env("STAGE4_ARCHIVE_BASE_URL", stage4["archive_base_url"])
        ).rstrip("/"),
        stage4_data_dir=_path(root, env("STAGE4_DATA_DIR", stage4["data_dir"])),
        stage4_download_jobs=int(env("STAGE4_DOWNLOAD_JOBS", stage4["download_jobs"])),
        stage4_realtime_lookback_days=int(
            env("STAGE4_REALTIME_LOOKBACK_DAYS", stage4["realtime_lookback_days"])
        ),
        stage4_archive_lag_days=int(env("STAGE4_ARCHIVE_LAG_DAYS", stage4["archive_lag_days"])),
        stage4_retries=int(env("STAGE4_RETRIES", stage4["retries"])),
        stage4_connect_timeout=int(
            env("STAGE4_CONNECT_TIMEOUT_SECONDS", stage4["connect_timeout_seconds"])
        ),
        stage4_read_timeout=int(env("STAGE4_READ_TIMEOUT_SECONDS", stage4["read_timeout_seconds"])),
        stage4_wgrib2=str(env("STAGE4_WGRIB2", stage4["wgrib2"])),
        prism_base_url=str(env("PRISM_BASE_URL", prism["base_url"])).rstrip("/"),
        prism_data_dir=_path(root, env("PRISM_DATA_DIR", prism["data_dir"])),
        prism_variables=tuple(str(env("PRISM_VARIABLES", ",".join(prism["variables"]))).split(",")),
        prism_refresh_days=int(env("PRISM_REFRESH_DAYS", prism["refresh_days"])),
        prism_lag_days=int(env("PRISM_LAG_DAYS", prism["lag_days"])),
        prism_retries=int(env("PRISM_RETRIES", prism["retries"])),
        prism_connect_timeout=int(
            env("PRISM_CONNECT_TIMEOUT_SECONDS", prism["connect_timeout_seconds"])
        ),
        prism_read_timeout=int(env("PRISM_READ_TIMEOUT_SECONDS", prism["read_timeout_seconds"])),
        prism_request_delay=float(
            env("PRISM_REQUEST_DELAY_SECONDS", prism["request_delay_seconds"])
        ),
        hrrr_base_url=str(env("HRRR_BASE_URL", hrrr["base_url"])).rstrip("/"),
        hrrr_data_dir=_path(root, env("HRRR_DATA_DIR", hrrr["data_dir"])),
        hrrr_download_jobs=int(env("HRRR_DOWNLOAD_JOBS", hrrr["download_jobs"])),
        hrrr_lag_days=int(env("HRRR_LAG_DAYS", hrrr["lag_days"])),
        hrrr_retries=int(env("HRRR_RETRIES", hrrr["retries"])),
        hrrr_connect_timeout=int(
            env("HRRR_CONNECT_TIMEOUT_SECONDS", hrrr["connect_timeout_seconds"])
        ),
        hrrr_read_timeout=int(env("HRRR_READ_TIMEOUT_SECONDS", hrrr["read_timeout_seconds"])),
        hrrr_wgrib2=str(env("HRRR_WGRIB2", hrrr["wgrib2"])),
        mrms_base_url=str(env("MRMS_BASE_URL", mrms["base_url"])).rstrip("/"),
        mrms_data_dir=_path(root, env("MRMS_DATA_DIR", mrms["data_dir"])),
        mrms_products=tuple(str(env("MRMS_PRODUCTS", ",".join(mrms["products"]))).split(",")),
        mrms_download_jobs=int(env("MRMS_DOWNLOAD_JOBS", mrms["download_jobs"])),
        mrms_realtime_lookback_days=int(
            env("MRMS_REALTIME_LOOKBACK_DAYS", mrms["realtime_lookback_days"])
        ),
        mrms_retries=int(env("MRMS_RETRIES", mrms["retries"])),
        mrms_connect_timeout=int(
            env("MRMS_CONNECT_TIMEOUT_SECONDS", mrms["connect_timeout_seconds"])
        ),
        mrms_read_timeout=int(env("MRMS_READ_TIMEOUT_SECONDS", mrms["read_timeout_seconds"])),
        mrms_wgrib2=str(env("MRMS_WGRIB2", mrms["wgrib2"])),
    )
