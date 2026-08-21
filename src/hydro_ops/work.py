"""Temporary workspace selection, preferring node-local SLURM scratch."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from hydro_ops.config import Settings

LOG = logging.getLogger(__name__)


def temporary_work_root(settings: Settings, task: str) -> Path:
    user = os.getenv("SLURM_JOB_USER")
    job_id = os.getenv("SLURM_JOB_ID")
    safe = re.compile(r"^[A-Za-z0-9_.-]+$")
    if user and job_id and safe.fullmatch(user) and safe.fullmatch(job_id):
        root = Path("/scratch") / user / f"job_{job_id}" / task
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError as error:
            LOG.warning(
                "Node scratch unavailable at %s; using project work directory: %s", root, error
            )
    fallback = settings.work_root / task
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
