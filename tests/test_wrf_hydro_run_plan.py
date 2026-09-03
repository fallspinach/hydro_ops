from datetime import UTC, datetime

import pytest

from hydro_ops.wrf_hydro.run_plan import plan_operational_run


def test_daily_operational_run_crosses_two_forcing_chunks() -> None:
    plan = plan_operational_run(datetime(2026, 9, 1, tzinfo=UTC), 24)
    assert plan.start == "2026-09-01T00:00:00Z"
    assert plan.end == "2026-09-02T00:00:00Z"
    assert plan.forcing_endpoint_start == "2026-09-01T01:00:00Z"
    assert plan.forcing_endpoint_end == "2026-09-02T00:00:00Z"
    assert plan.forcing_calendar_days == ["2026-09-01", "2026-09-02"]
    assert plan.daily_product_days == ["2026-09-01"]
    assert plan.t0_output is False


def test_multiday_run_lists_terminal_forcing_chunk() -> None:
    plan = plan_operational_run(datetime(2026, 12, 31, tzinfo=UTC), 48)
    assert plan.forcing_calendar_days == ["2026-12-31", "2027-01-01", "2027-01-02"]
    assert plan.daily_product_days == ["2026-12-31", "2027-01-01"]


@pytest.mark.parametrize(
    ("start", "hours"),
    [
        (datetime(2026, 9, 1, tzinfo=UTC).replace(tzinfo=None), 24),
        (datetime(2026, 9, 1, 1, tzinfo=UTC), 24),
        (datetime(2026, 9, 1, tzinfo=UTC), 25),
    ],
)
def test_noncanonical_operational_plan_is_rejected(start: datetime, hours: int) -> None:
    with pytest.raises(ValueError):
        plan_operational_run(start, hours)
