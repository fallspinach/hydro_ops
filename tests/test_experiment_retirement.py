from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETIRED = (
    'test_nrt_baseline_schema.sh',
    'test_nrt_schema_operational.sh',
    'test_hourly_layout_nwm.sh',
    'test_nwm_nrt_midatlantic_20260310_48h.sh',
    'test_nwm_monthly_prism_periods.sh',
)


@pytest.mark.parametrize('name', RETIRED)
def test_retired_launcher_is_removed_and_documented(name):
    assert not (ROOT / 'slurm' / name).exists()
    assert not (ROOT / 'archive/experiments/slurm' / name).exists()
    assert name in (ROOT / 'docs/experiment_catalog.md').read_text()
