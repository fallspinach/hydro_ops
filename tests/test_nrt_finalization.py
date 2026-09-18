import importlib.util
from pathlib import Path

import pytest


def test_finalization_requires_writer_and_reuse_acceptance():
    path = Path(__file__).parents[1] / "bin/finalize_nrt_operational_test.py"
    spec = importlib.util.spec_from_file_location("finalize", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.summary({"status": "passed"})
    cycle = {"status": "passed", "errors": [], "days": [{"status": "unchanged"}],
             "latency": {"worker_seconds": 4000, "launch_to_publication_seconds": 4100}}
    report = {"status": "passed", "optimized_writers": {"status": "passed"}, "baseline_reuse": "passed",
              "first_cycle": cycle, "repeat_cycle": cycle, "job_id": "123", "start": "2026-09-14",
              "end": "2026-09-15", "actual_gfs_hours": 48, "first_worker_under_hour": False}
    assert "under one hour: False" in module.summary(report)
    report["baseline_reuse"] = "failed"
    with pytest.raises(ValueError):
        module.summary(report)
