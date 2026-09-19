from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_repair_does_not_skip_unaggregated_complete_hours(tmp_path):
    for hour in range(24):
        path = tmp_path / f'2011/10/29/20111029{hour:02d}.LDASIN_DOMAIN1'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'present')
        path.with_name(path.name + '.manifest.json').write_text('{}')
    base = [sys.executable, str(PROJECT_ROOT / 'bin/submit_forcing_days.py'),
            '--start', '2011-10-29', '--end', '2011-10-29', '--output-root', str(tmp_path),
            '--missing-only', '--dry-run']
    daily = subprocess.run(base, cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
    assert 'eligible_days=1' in daily.stdout
    hourly = subprocess.run([*base, '--keep-hourly'], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
    assert 'eligible_days=0' in hourly.stdout


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
