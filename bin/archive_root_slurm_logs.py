"""Consolidate inactive root SLURM logs into a verified, recoverable tar archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    active = subprocess.check_output(
        ["squeue", "--noheader", "--format=%i"], text=True
    )
    active_ids = {line.strip().split("_")[0] for line in active.splitlines()}
    tracked = set(subprocess.check_output(
        ["git", "ls-files", "--", "slurm-*.out"], cwd=root, text=True
    ).splitlines())
    paths = []
    for path in sorted(root.glob("slurm-*.out")):
        match = re.fullmatch(r"slurm-(\d+)(?:_\d+)?\.out", path.name)
        if (match and match[1] not in active_ids and path.name not in tracked
                and path.is_file() and not path.is_symlink()):
            paths.append(path)
    print(json.dumps({"eligible_logs": len(paths),
                      "bytes": sum(path.stat().st_size for path in paths)}), flush=True)
    if not args.apply or not paths:
        return 0
    archive_dir = root / "logs/archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"slurm-root-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.tar.gz"
    partial = archive.with_suffix(archive.suffix + ".part")
    hashes = {}
    with tarfile.open(partial, "w:gz") as bundle:
        for path in paths:
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            bundle.add(path, arcname=path.name, recursive=False)
    with tarfile.open(partial, "r:gz") as bundle:
        verified = set()
        for member in bundle:
            content = bundle.extractfile(member)
            if content is None or hashlib.sha256(content.read()).hexdigest() != hashes[member.name]:
                raise RuntimeError(f"Archive verification failed: {member.name}")
            verified.add(member.name)
        if verified != set(hashes):
            raise RuntimeError("Archive member list differs from selected logs")
    partial.replace(archive)
    removed = 0
    for path in paths:
        # Preserve anything modified after collection, even if it was inactive earlier.
        if hashlib.sha256(path.read_bytes()).hexdigest() == hashes[path.name]:
            path.unlink()
            removed += 1
    report = {"archive": str(archive), "archived_logs": len(paths),
              "removed_originals": removed, "archive_bytes": archive.stat().st_size,
              "sha256": hashes}
    archive.with_suffix(archive.suffix + ".manifest.json").write_text(
        json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "sha256"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
