"""Canonical paths and isolation checks for published forcing streams."""

from __future__ import annotations

from pathlib import Path

FORCING_STREAMS = ("nrt", "retro")


def forcing_stream_root(project_root: Path, stream: str) -> Path:
    """Return the canonical domain and stream root."""
    if stream not in FORCING_STREAMS:
        raise ValueError(f"Unknown forcing stream: {stream}")
    return project_root / "forcing/outputs/conus" / stream


def baseline_root(project_root: Path) -> Path:
    """Return the canonical reusable baseline root."""
    return project_root / "forcing/outputs/conus/baseline"


def validate_stream_output_root(root: Path, stream: str) -> Path:
    """Reject a destination that could mix one stream into another."""
    if stream not in FORCING_STREAMS:
        raise ValueError(f"Unknown forcing stream: {stream}")
    resolved = root.resolve()
    if resolved.name != stream:
        raise ValueError(
            f"The {stream!r} stream output root must end in '/{stream}', got: {resolved}"
        )
    other = "retro" if stream == "nrt" else "nrt"
    if other in resolved.parts:
        raise ValueError(f"The {stream!r} stream cannot be published below a {other!r} path")
    return resolved
