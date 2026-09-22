"""Read-only plan for adding a temporal-resolution level to forcing archives.

Intentionally has no execute mode: queue inspection alone cannot establish that
interactive readers, cron launches, or frozen batch scripts are safe to migrate.
"""

import argparse
import getpass
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def inventory(root):
    moves, conflicts, streams = [], [], []
    top = root / "forcing/outputs"
    for domain in sorted(top.iterdir() if top.is_dir() else []):
        if not domain.is_dir() or domain.is_symlink():
            continue
        for name in ("baseline", "nrt", "retro"):
            stream = domain / name
            if not stream.is_dir():
                continue
            streams.append(str(stream.relative_to(root)))
            if stream.is_symlink() or (stream / "hourly").is_symlink():
                conflicts.append(f"Symlink requires manual review: {stream}")
                continue
            if (stream / "hourly").exists() and not (stream / "hourly").is_dir():
                conflicts.append(f"Hourly destination is not a directory: {stream}")
                continue
            for source in sorted(stream.iterdir()):
                if not re.fullmatch(r"\d{4}", source.name):
                    continue
                destination = stream / "hourly" / source.name
                if source.is_symlink() or not source.is_dir():
                    conflicts.append(f"Unexpected year entry: {source}")
                elif destination.exists() or destination.is_symlink():
                    conflicts.append(f"Destination already exists: {destination}")
                else:
                    moves.append(
                        {
                            "source": str(source.relative_to(root)),
                            "destination": str(destination.relative_to(root)),
                            "operation": "rename_directory_including_manifests",
                        }
                    )
    return {"stream_roots": streams, "proposed_moves": moves, "conflicts": conflicts}


def queue():
    try:
        result = subprocess.run(
            ["squeue", "--noheader", "--user", getpass.getuser(), "--format=%i|%j|%T|%E"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Include ALL jobs, including pending readers/controllers; no name-based safety filter.
        return {"checked": True, "jobs_requiring_review": result.stdout.splitlines()}
    except (OSError, subprocess.SubprocessError) as error:
        return {"checked": False, "error": str(error)}


def references(root):
    result = subprocess.run(
        [
            "rg",
            "-l",
            r"forcing/outputs|forcing_root|forcing-root|baseline_root|baseline-root",
            "bin",
            "slurm",
            "config",
            "src",
            "tests",
            "docs",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return result.stdout.splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project_root.resolve()
    report = inventory(root)
    report.update(
        schema_version=1,
        mode="plan-only",
        migration_authorized=False,
        created_at_utc=datetime.now(UTC).isoformat(),
        project_root=str(root),
        scheduler=queue(),
        reference_files_requiring_review=references(root),
        required_gates=[
            "forcing completion and coverage audits",
            "NWM reader checkpoint boundary and pending-job review",
            "cron/controller/interactive launch freeze",
            "configuration and manifest rewrite reviewed",
            "inventory and rollback journal saved",
            "post-migration forcing and NWM read smoke tests",
        ],
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
