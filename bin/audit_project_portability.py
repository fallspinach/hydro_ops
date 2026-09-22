"""Read-only source path inventory; no runtime manifests or data are rewritten."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--old-root", help="Literal old location to search; defaults to project root"
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    old = args.old_root or str(root)
    hits = []
    for directory in ("bin", "slurm", "src", "config", "cron"):
        for p in sorted((root / directory).rglob("*")):
            if (
                not p.is_file()
                or p.is_symlink()
                or "__pycache__" in p.parts
                or p.name in ("local.env", "local.toml", "site.local.env")
                or p.suffix not in (".py", ".sh", ".sbatch", ".toml", ".env", ".crontab", ".in")
            ):
                continue
            for number, line in enumerate(p.read_text().splitlines(), 1):
                categories = []
                if old in line:
                    categories.append("project_location")
                if "/home/" in line or "/cm/" in line:
                    categories.append("site_location")
                if categories:
                    hits.append(
                        {"file": str(p.relative_to(root)), "line": number, "categories": categories}
                    )
    print(
        json.dumps(
            {
                "mode": "read-only",
                "matches": hits,
                "match_count": len(hits),
                "scope": "source/config only; excludes local secrets, manifests, binaries and symlink targets",
                "note": "Site config and generated cron paths are expected. Review remaining scripts individually.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
