from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from hydro_ops.forcing.nrt_freshness import plan_hours, publication_order


def test_partial_day_needs_gfs_but_not_prism_or_mrms():
    start = datetime(2026, 9, 24, tzinfo=UTC)
    checked = []
    def select(t):
        return SimpleNamespace(valid_time=t, product="nldas2" if t.hour < 2 else "hrrr")
    def gfs(t):
        checked.append(t.hour)
        if t.hour == 4:
            raise FileNotFoundError("GFS gap")
    result = plan_hours(start, start + timedelta(hours=8), select, gfs)
    assert result["latest_source_ready_hour"] == (start + timedelta(hours=3)).isoformat()
    assert checked == [2, 3, 4]
    assert result["status"] == "planned_not_published"


def test_gap_is_not_skipped_and_errors_are_not_hidden():
    start = datetime(2026, 9, 24, tzinfo=UTC)
    def missing(t):
        raise FileNotFoundError("primary gap")
    assert plan_hours(start, start, missing, None)["hours"] == []
    def invalid(t):
        raise ValueError("corrupt source")
    with pytest.raises(ValueError, match="corrupt"):
        plan_hours(start, start, invalid, None)
    with pytest.raises(ValueError, match="UTC"):
        plan_hours(start.replace(tzinfo=None), start, missing, None)


def test_extension_before_revisions():
    days = [date(2026, 9, d) for d in range(20, 25)]
    assert publication_order(days[0], days[-1], days[:3]) == days[3:] + days[2::-1]
