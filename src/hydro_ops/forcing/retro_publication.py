"""Strict completion and scratch checks for the remaining retro campaign."""

import json
import os
import shutil
from pathlib import Path

from netCDF4 import Dataset, num2date

POLICY = "nldas2_seven_met_static_envelope_v4"
AUDIT = "all_records_active_retained_unchanged_outside_envelope_missing_v4"


def check_scratch(path):
    required = int(os.environ.get("HYDRO_OPS_MIN_SCRATCH_FREE_GB", "0")) * 10**9
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < required:
        raise RuntimeError(f"Insufficient scratch before processing: {path}: {free} < {required} bytes")


def complete(path):
    """Reject partial PRISM-only outputs and stale publication sidecars."""
    try:
        project = Path(__file__).resolve().parents[3]
        mask = project / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"
        with Dataset(mask) as grid:
            mask_hash = str(grid.keep_sha256)
        with Dataset(path) as data:
            if (getattr(data, "forcing_domain_policy", "") != POLICY
                    or getattr(data, "forcing_domain_content_audit", "") != AUDIT
                    or getattr(data, "forcing_static_mask_sha256", "") != mask_hash):
                return False
            stamp = path.name[:8]
            values = num2date(data["time"][:], data["time"].units)
            if [v.strftime("%Y%m%d%H%M%S") for v in values] != [stamp + f"{h:02d}0000" for h in range(24)]:
                return False
            if stamp >= "20200701" and not getattr(data, "cnrfc_stage4_policy", ""):
                return False
        manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text())
        envelope = manifest["static_envelope"]
        stat = path.stat()
        identity = {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        report = json.loads(Path(envelope["audit"]).read_text())
        return (envelope["published_identity"] == identity
                and envelope["mask_sha256"] == mask_hash
                and report["status"] == "published"
                and report["published_identity"] == identity)
    except (OSError, ValueError, RuntimeError, KeyError, AttributeError):
        return False
