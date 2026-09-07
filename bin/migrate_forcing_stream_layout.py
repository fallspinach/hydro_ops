#!/usr/bin/env python3
"""Compatibility entry point for the completed project-layout migration."""

from pathlib import Path


def main() -> int:
    canonical = Path("forcing/outputs/conus")
    if not canonical.is_dir():
        raise RuntimeError("Run bin/migrate_project_layout.py to create the canonical layout")
    print(f"Forcing streams already use the canonical layout: {canonical.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
