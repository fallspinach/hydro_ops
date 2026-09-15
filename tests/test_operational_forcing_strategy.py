import pytest

from hydro_ops.forcing.operational_strategy import latency, plan, work_for

AS_OF = "2026-08-26T02:00:00Z"


def test_precipitation_revision_does_not_request_meteorology():
    tasks = work_for(["stage4"])
    assert "precipitation_selection_remap_and_cnrf_constraint" in tasks
    assert "precipitation_prism_reconciliation_if_eligible" in tasks
    assert not any("meteorology" in t or "humidity" in t for t in tasks)
    assert not any("remap" in t for t in work_for(["prism_ppt"]))
    assert any("humidity" in t for t in work_for(["prism_temperature"]))
    assert any("meteorology" in t for t in work_for(["nldas2"]))
    assert work_for([]) == []


def test_lane_separation_newest_first_and_no_unstable_retro():
    events = [
        {"day": "2026-08-10", "changes": ["prism_ppt"]},
        {"day": "2026-08-24", "changes": ["new_hours"]},
        {"day": "2026-08-25", "changes": ["new_hours"]},
        {"day": "2026-02-01", "stream": "retro", "changes": ["missing_output"]},
    ]
    fast = plan(events, AS_OF)
    assert [i["day"] for i in fast["work"]] == ["2026-08-25", "2026-08-24"]
    assert len(fast["deferred"]) == 2
    assert not plan(events, AS_OF, lane="daily-retro")["work"]
    events[-1]["prism_stable"] = True
    assert len(plan(events, AS_OF, lane="daily-retro")["work"]) == 1
    assert not fast["execution_enabled"]


def test_latency_counts_preworker_delay_and_exact_deadline():
    result = latency(AS_OF, "2026-08-26T02:40:00Z", "2026-08-26T03:00:00Z")
    assert result["launch_to_publication_seconds"] == 3600
    assert result["worker_seconds"] == 1200
    assert result["latency_status"] == "deadline_missed"
    with pytest.raises(ValueError):
        latency("2026-08-26T02:00:00", AS_OF, AS_OF)
    with pytest.raises(ValueError):
        work_for(["unknown_source"])
