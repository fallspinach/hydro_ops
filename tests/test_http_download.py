import os
from datetime import UTC, datetime
from email.utils import format_datetime

from hydro_ops.download.http import apply_remote_mtime, local_matches_remote


def headers(size: int, modified: datetime) -> dict[str, str]:
    return {"Content-Length": str(size), "Last-Modified": format_datetime(modified)}


def test_local_matches_same_or_older_remote(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"content")
    modified = datetime(2026, 8, 20, tzinfo=UTC)
    os.utime(path, (modified.timestamp(), modified.timestamp()))
    assert local_matches_remote(path, headers(7, modified))


def test_local_does_not_match_newer_or_different_size_remote(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"content")
    modified = datetime(2026, 8, 20, tzinfo=UTC)
    os.utime(path, (modified.timestamp(), modified.timestamp()))
    assert not local_matches_remote(path, headers(7, datetime(2026, 8, 21, tzinfo=UTC)))
    assert not local_matches_remote(path, headers(8, modified))


def test_apply_remote_mtime(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"content")
    modified = datetime(2026, 8, 20, tzinfo=UTC)
    apply_remote_mtime(path, headers(7, modified))
    assert path.stat().st_mtime == modified.timestamp()
