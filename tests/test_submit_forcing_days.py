from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_submission_reserves_node_local_scratch(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "bin/submit_forcing_days.py"),
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-01",
            "--output-root",
            str(tmp_path / "baseline"),
            "--dry-run",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--tmp=120000" in completed.stdout


def test_daily_submission_accepts_scratch_override(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "bin/submit_forcing_days.py"),
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-01",
            "--output-root",
            str(tmp_path / "baseline"),
            "--tmp-mb",
            "150000",
            "--dry-run",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--tmp=150000" in completed.stdout
