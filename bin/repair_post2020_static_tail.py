"""Inspect and clip the remaining early post-2020 retro files, without rebuilding forcing."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import apply_static_forcing_mask as mask_writer
from netCDF4 import Dataset, num2date

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "forcing/outputs/conus/retro"
MASK = PROJECT / "forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc"


def inspect(path):
    day = date.fromisoformat(f"{path.name[:4]}-{path.name[4:6]}-{path.name[6:8]}")
    with Dataset(path) as data:
        attrs = {
            k: str(getattr(data, k, ""))
            for k in (
                "forcing_domain_policy",
                "forcing_domain_content_audit",
                "cnrfc_stage4_policy",
                "forcing_recovery_policy",
                "archive_granularity",
                "prism_reconciliation_accepted",
            )
        }
        t = data["time"]
        stamps = num2date(t[:], t.units, getattr(t, "calendar", "standard"))
        if [(x.year, x.month, x.day, x.hour, x.minute, x.second) for x in stamps] != [
            (day.year, day.month, day.day, h, 0, 0) for h in range(24)
        ]:
            raise ValueError(f"Not a complete UTC calendar day: {path}")
        if (
            not attrs["cnrfc_stage4_policy"]
            or attrs["forcing_recovery_policy"] != "cnrfc_prism_domain_rebuild_v1"
            or attrs["archive_granularity"] != "utc_calendar_day"
            or attrs["prism_reconciliation_accepted"].lower() != "true"
        ):
            raise ValueError(f"Missing accepted CNRFC/PRISM provenance: {path}: {attrs}")
        if attrs["forcing_domain_policy"] not in (
            "nldas2_rectangle_nearest_valid_v1",
            "nldas2_active_gaps_preserve_inactive_v3",
        ):
            raise ValueError(f"Unexpected policy; manual review required: {path}: {attrs}")
    return attrs


def plan(campaign):
    records = []
    day = date(2020, 10, 14)
    while day <= date(2021, 10, 19):
        path = ROOT / f"{day:%Y/%m/%Y%m%d}.LDASIN_DOMAIN1"
        manifest = json.loads(path.with_name(path.name + ".manifest.json").read_text())
        if "static_envelope" not in manifest:
            if manifest.get("verified") is not True:
                raise ValueError(f"Unverified original publication: {path}")
            before = mask_writer.identity(path)
            metadata = inspect(path)
            if before != mask_writer.identity(path):
                raise ValueError(f"Source changed during inspection: {path}")
            records.append({"path": str(path), "identity": before, "metadata": metadata})
        day += timedelta(days=1)
    if len(records) != 308:
        raise ValueError(
            f"Inventory changed: expected 308, got {len(records)}; review before submitting"
        )
    campaign.mkdir(parents=True, exist_ok=False)
    batches = [records[i : i + 14] for i in range(0, len(records), 14)]
    (campaign / "tasks.jsonl").write_text("".join(json.dumps(b) + "\n" for b in batches))
    (campaign / "inspection.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "files": len(records),
                "scope": "308 earlier CNRFC-corrected retro files; static mask only",
                "records": records,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps({"status": "inspection_passed", "files": len(records), "tasks": len(batches)}),
        flush=True,
    )
    return len(batches)


def process(record, campaign):
    path = Path(record["path"])
    state = campaign / "audit"
    state.mkdir(exist_ok=True)
    with (state / (path.name + ".publication.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report_path = state / (path.name[:8] + ".json")
        if report_path.exists():
            report = json.loads(report_path.read_text())
            if report.get("status") == "published" and report.get(
                "published_identity"
            ) == mask_writer.identity(path):
                return {"path": str(path), "status": "already_published"}
        if mask_writer.identity(path) != record["identity"]:
            raise ValueError(f"Input changed since inspection: {path}")
        scratch = (
            Path("/scratch") / os.environ["SLURM_JOB_USER"] / f"job_{os.environ['SLURM_JOB_ID']}"
        )
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="post2020-mask-tail-", dir=scratch) as tmp:
            work = Path(tmp)
            staged_root = work / "candidate"
            staged = staged_root / path.relative_to(ROOT)
            staged.parent.mkdir(parents=True)
            shutil.copy2(path, staged)
            shutil.copy2(
                path.with_name(path.name + ".manifest.json"),
                staged.with_name(staged.name + ".manifest.json"),
            )
            if mask_writer.file_hash(path) != mask_writer.file_hash(staged):
                raise ValueError("Staging checksum mismatch")
            local_state = work / "audit"
            mask_writer.publish(
                staged, MASK, staged_root, local_state, work, staged_rebuild=True, fast=True
            )
            if mask_writer.identity(path) != record["identity"]:
                raise ValueError(f"Source changed before publication: {path}")
            shutil.copy2(local_state / (path.name[:8] + ".json"), report_path)
            mask_writer.transfer_publication(staged, path, state)
        return {"path": str(path), "status": "published"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--mode", choices=["submit", "worker", "audit"], required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    if args.mode == "submit":
        n = plan(campaign)
        common = ["sbatch", "--parsable", "--partition=shared-128", "--nodes=1", "--ntasks=1"]
        command = common + [
            "--cpus-per-task=12",
            "--tmp=120000",
            "--time=12:00:00",
            f"--array=0-{n - 1}%4",
            "--job-name=forcing-retro-2020-2021-cnrfcdone-staticmask-tail",
            f"--output={PROJECT}/forcing/logs/post2020-mask-tail-%A_%a.out",
            "--wrap",
            f"{sys.executable} {Path(__file__).resolve()} --campaign {campaign} --mode worker",
        ]
        job = subprocess.check_output(command, text=True).strip().split(";")[0]
        audit = common + [
            "--cpus-per-task=2",
            "--time=01:00:00",
            f"--dependency=afterany:{job}",
            "--job-name=forcing-retro-2020-2021-staticmask-tail-final-audit",
            f"--output={PROJECT}/forcing/logs/post2020-mask-tail-audit-%j.out",
            "--wrap",
            f"{sys.executable} {Path(__file__).resolve()} --campaign {campaign} --mode audit",
        ]
        audit_job = subprocess.check_output(audit, text=True).strip()
        result = {"job": job, "audit_job": audit_job, "command": command}
        (campaign / "submission.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    elif args.mode == "worker":
        records = json.loads(
            (campaign / "tasks.jsonl")
            .read_text()
            .splitlines()[int(os.environ["SLURM_ARRAY_TASK_ID"])]
        )

        # NetCDF is not thread-safe: independent subprocesses, bounded at two writers.
        def run_record(record):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--campaign",
                str(campaign),
                "--mode",
                "audit",
            ]
            env = {**os.environ, "MASK_TAIL_RECORD": json.dumps(record)}
            for attempt in range(2):
                if subprocess.run(command, env=env, check=False).returncode == 0:
                    return None
            return record["path"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            failures = [x for x in pool.map(run_record, records) if x]
        if failures:
            raise RuntimeError(f"Failed files: {failures}")
    elif os.environ.get("MASK_TAIL_RECORD"):
        print(json.dumps(process(json.loads(os.environ["MASK_TAIL_RECORD"]), campaign)), flush=True)
    else:
        failures = []
        count = 0
        for line in (campaign / "tasks.jsonl").read_text().splitlines():
            for record in json.loads(line):
                p = Path(record["path"])
                try:
                    m = json.loads(p.with_name(p.name + ".manifest.json").read_text())[
                        "static_envelope"
                    ]
                    a = json.loads((campaign / "audit" / f"{p.name[:8]}.json").read_text())
                    assert a["status"] == "published"
                    assert (
                        a["published_identity"]
                        == m["published_identity"]
                        == mask_writer.identity(p)
                    )
                    assert (
                        m["valid_outside_mask"] == 0
                        and m["active_values_unchanged"]
                        and m["retained_values_unchanged"]
                    )
                    with Dataset(p) as d:
                        assert d.forcing_domain_policy == mask_writer.POLICY
                        assert d.cnrfc_stage4_policy == record["metadata"]["cnrfc_stage4_policy"]
                        assert str(d.prism_reconciliation_accepted).lower() == "true"
                    count += 1
                except (OSError, ValueError, KeyError, AttributeError, AssertionError) as e:
                    failures.append({"path": str(p), "error": repr(e)})
        report = {
            "status": "passed" if not failures else "failed",
            "verified_files": count,
            "failures": failures,
        }
        (campaign / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
