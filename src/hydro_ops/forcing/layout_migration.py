"""Pure path mapping helpers for the hourly-resolution cutover.

The migration utility uses these helpers; they perform no filesystem changes.
Stream roots without a year require caller-specific review, not blanket rewriting.
"""
import re
from pathlib import Path, PurePosixPath


def map_dated_forcing_path(value: str, project: Path) -> str:
    """Insert hourly only before a four-digit year under domain/stream."""
    prefix = str(project.resolve())+'/'
    absolute = value.startswith(prefix)
    relative = value[len(prefix):] if absolute else value
    if relative.startswith('/'):
        return value
    parts = PurePosixPath(relative).parts
    if (len(parts) < 5 or parts[:2] != ('forcing', 'outputs')
            or parts[3] not in {'baseline', 'nrt', 'retro'}
            or not re.fullmatch(r'\d{4}', parts[4])):
        return value
    mapped = '/'.join((*parts[:4], 'hourly', *parts[4:]))
    return prefix+mapped if absolute else mapped
