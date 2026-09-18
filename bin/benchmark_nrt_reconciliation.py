"""Paired private warm-baseline revision replay; compare all decoded output fields."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.gfs_publication import _atomic_json
from hydro_ops.forcing.nrt_cycle import RecentNrt, identity


def seed(source, destination):
    """Private names sharing read-only inputs; production replaces files atomically."""
    for path in source.glob("*/*/*.LDASIN_DOMAIN1"):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(path, target)
        receipt = path.with_name(path.name + ".nrt-receipt.json")
        record = json.loads(receipt.read_text())
        record["published_identity"] = identity(target)
        if destination.name == "nrt":
            # Replay notification of a PRISM revision, without mutating sources.
            record["input_fingerprint"] = "test-revision-invalidated"
        _atomic_json(target.with_name(target.name + ".nrt-receipt.json"), record)


def compare(left, right):
    with Dataset(left) as a, Dataset(right) as b:
        a.set_auto_maskandscale(False)
        b.set_auto_maskandscale(False)
        if set(a.variables) != set(b.variables):
            raise ValueError("Variable sets differ")
        for name, var in a.variables.items():
            other = b[name]
            if var.shape != other.shape or var.dimensions != other.dimensions or var.dtype != other.dtype:
                raise ValueError(f"Schema mismatch: {name}")
            if set(var.ncattrs()) != set(other.ncattrs()):
                raise ValueError(f"Attribute set mismatch: {name}")
            for attr in var.ncattrs():
                np.testing.assert_array_equal(var.getncattr(attr), other.getncattr(attr))
            indices = range(var.shape[0]) if var.dimensions and var.dimensions[0] == "time" else [Ellipsis]
            for index in indices:
                if np.asarray(var[index]).tobytes() != np.asarray(other[index]).tobytes():
                    raise ValueError(f"Field mismatch: {name}, {index}")
        for attr in ("forcing_domain_policy", "cnrfc_stage4_policy", "prism_reconciliation_accepted"):
            if getattr(a, attr, None) != getattr(b, attr, None):
                raise ValueError(f"Policy mismatch: {attr}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--campaign", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    campaign = args.campaign.resolve()
    campaign.mkdir(parents=True, exist_ok=False)
    source = json.loads((args.source / "submission.json").read_text())
    result = {"status": "running", "scope": "controlled PRISM receipt invalidation, unchanged source values",
              "production_switch": False, "arms": {}}
    _atomic_json(campaign / "acceptance.json", result)
    try:
        # Refresh may have invalidated old receipts. Warm private baselines once
        # outside both measured arms, then give each arm the same accepted inputs.
        prepared = campaign / "prepared/baseline"
        seed(args.source / "baseline", prepared)
        work = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}")
        engine = RecentNrt(root, work, datetime.now(UTC), baseline_root=prepared,
                           output_root=campaign / "prepared/nrt")
        begin = time.monotonic()
        for path in sorted(prepared.glob("*/*/*.LDASIN_DOMAIN1")):
            engine.baseline_day(date.fromisoformat(path.name[:8]))
        result["preparation_seconds"] = time.monotonic() - begin
        _atomic_json(campaign / "acceptance.json", result)
        for mode in ("reference", "optimized"):
            arm = campaign / mode
            arm.mkdir()
            for kind in ("baseline", "nrt"):
                seed(prepared if kind == "baseline" else args.source / kind, arm / kind)
            baseline_before = {str(p): identity(p) for p in (arm / "baseline").glob("*/*/*.LDASIN_DOMAIN1")}
            _atomic_json(arm / "submission.json", {**source, "requested_at": datetime.now(UTC).isoformat()})
            env = dict(os.environ)
            env.update(HYDRO_OPS_ARCHIVE_CHUNKS="1" if mode == "optimized" else "0",
                       HYDRO_OPS_NRT_REUSE_WINDOWS="1" if mode == "optimized" else "0",
                       HYDRO_OPS_PROFILE_DIRECTORY=str(arm / "timings"), HYDRO_OPS_PROFILE_TIMING_ONLY="1",
                       PYTHONPATH=str(root / "tools/forcing_profile") + os.pathsep + str(root / "src"))
            begin = time.monotonic()
            subprocess.run([sys.executable, "-u", str(root / "bin/test_nrt_operational_cycle.py"),
                            "--campaign", str(arm)], env=env, check=True)
            result["arms"][mode] = {"elapsed_seconds": time.monotonic() - begin,
                                      "baseline_files_rebuilt": sum(identity(Path(p)) != old for p, old in baseline_before.items()),
                                      "acceptance": json.loads((arm / "acceptance.json").read_text())}
            _atomic_json(campaign / "acceptance.json", result)
        if any(arm["baseline_files_rebuilt"] for arm in result["arms"].values()):
            raise RuntimeError("Sources changed after preparation; warm-baseline timing is not comparable")
        checked = []
        for left in (campaign / "reference/nrt").glob("*/*/*.LDASIN_DOMAIN1"):
            relative = left.relative_to(campaign / "reference/nrt")
            compare(left, campaign / "optimized/nrt" / relative)
            checked.append(str(relative))
        if len(checked) != 2:
            raise ValueError("Expected two comparison days")
        result.update(status="passed", compared_days=checked)
    except Exception as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        _atomic_json(campaign / "acceptance.json", result)


if __name__ == "__main__":
    main()
