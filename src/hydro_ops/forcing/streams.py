"""Canonical paths and isolation checks for published forcing streams."""

from __future__ import annotations

from pathlib import Path

FORCING_STREAMS = ("nrt", "retro")


def forcing_stream_root(project_root: Path, stream: str) -> Path:
    """Return the canonical root, falling back during the active layout migration."""
    if stream not in FORCING_STREAMS:
        raise ValueError(f"Unknown forcing stream: {stream}")
    canonical = project_root / "outputs/forcing/nwm" / stream
    legacy = project_root / "outputs/forcing/nwm_prism" / stream
    return canonical if canonical.exists() or not legacy.exists() else legacy


def baseline_root(project_root: Path) -> Path:
    """Return the canonical baseline root, with a safe pre-migration fallback."""
    legacy = project_root / "outputs/forcing/nwm"
    canonical = legacy / "baseline"
    return canonical if canonical.exists() else legacy


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
