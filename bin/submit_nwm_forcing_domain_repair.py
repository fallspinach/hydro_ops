#!/usr/bin/env python3
"""Submit resumable array repairs for published daily NWM forcing."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--max-concurrent", type=int, default=16)
    parser.add_argument("--tmp-mb", type=int, default=120000)
    parser.add_argument("--partition")
    parser.add_argument(
        "--include-all-existing",
        action="store_true",
        help="deprecated compatibility option; all existing files are content-validated",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="validate selected files without modifying them",
    )
    args = parser.parse_args()
    settings = load_settings()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    partition = args.partition or settings.slurm_partition
    pending: list[Path] = []
    day = args.start
    while day <= args.end:
        path = args.root / day.strftime("%Y/%m") / f"{day:%Y%m%d}.LDASIN_DOMAIN1"
        if path.is_file():
            # A policy attribute is provenance, not a completeness certificate.
            # Let every worker perform the full eight-field, all-record audit.
            pending.append(path.resolve())
        day += timedelta(days=1)
    print(f"found={len(pending)}", flush=True)
    if not pending or args.dry_run:
        return 0
    task_dir = args.project_root.resolve() / "logs" / "forcing_domain_repair"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / f"tasks_{args.start:%Y%m%d}_{args.end:%Y%m%d}.txt"
    temporary = task_file.with_suffix(".txt.part")
    temporary.write_text("".join(f"{path}\n" for path in pending))
    temporary.replace(task_file)
    exports = (
        f"ALL,HYDRO_OPS_PROJECT_ROOT={args.project_root.resolve()},"
        f"HYDRO_OPS_PYTHON={sys.executable},HYDRO_OPS_DOMAIN_REPAIR_TASK_FILE={task_file}"
    )
    if args.audit_only:
        exports += ",HYDRO_OPS_DOMAIN_AUDIT_ONLY=1"
    command = [
        "sbatch",
        f"--output={args.project_root.resolve()}/forcing/logs/domain-repair-%A_%a.out",
        f"--partition={partition}",
        f"--array=0-{len(pending) - 1}%{args.max_concurrent}",
        f"--tmp={args.tmp_mb}",
        (
            f"--job-name=nwm-domain-{'audit' if args.audit_only else 'repair'}-"
            f"{args.start:%Y%m%d}-{args.end:%Y%m%d}"
        ),
        f"--export={exports}",
        "slurm/repair_nwm_forcing_domain.py",
    ]
    (args.project_root.resolve() / "forcing/logs").mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, cwd=args.project_root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
