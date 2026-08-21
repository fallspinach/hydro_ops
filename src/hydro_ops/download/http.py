"""Shared HTTP metadata helpers for download freshness checks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from pathlib import Path


def remote_timestamp(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Last-Modified")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def local_matches_remote(path: Path, headers: Mapping[str, str]) -> bool:
    """Return true when local size and mtime match or exceed remote metadata."""
    try:
        stat = path.stat()
        remote_size = int(headers["Content-Length"])
    except (OSError, KeyError, TypeError, ValueError):
        return False
    modified = remote_timestamp(headers)
    return modified is not None and stat.st_size == remote_size and stat.st_mtime >= modified


def apply_remote_mtime(path: Path, headers: Mapping[str, str]) -> None:
    """Preserve the server modification time so later HEAD checks are meaningful."""
    modified = remote_timestamp(headers)
    if modified is not None:
        os.utime(path, (modified, modified))
