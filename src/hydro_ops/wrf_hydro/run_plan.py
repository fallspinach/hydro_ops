"""Restart- and forcing-aware WRF-Hydro operational run plans."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class OperationalRunPlan:
    start: str
    end: str
    hours: int
    forcing_endpoint_start: str
    forcing_endpoint_end: str
    forcing_calendar_days: list[str]
    daily_product_days: list[str]
    input_restart_time: str
    output_restart_time: str
    t0_output: bool = False
    day_definition: str = "model_interval"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def plan_operational_run(start: datetime, hours: int) -> OperationalRunPlan:
    if start.tzinfo is None or start.utcoffset() != timedelta(0):
        raise ValueError("Operational start must be timezone-aware UTC")
    start = start.astimezone(UTC)
    if start.minute or start.second or start.microsecond or start.hour != 0:
        raise ValueError("Operational start must be aligned to 00:00 UTC")
    if hours <= 0 or hours % 24:
        raise ValueError("Operational duration must be a positive multiple of 24 hours")
    end = start + timedelta(hours=hours)
    days = hours // 24
    forcing_days = [(start + timedelta(days=index)).date().isoformat() for index in range(days + 1)]
    product_days = [(start + timedelta(days=index)).date().isoformat() for index in range(days)]
    stamp = lambda value: value.isoformat().replace("+00:00", "Z")
    return OperationalRunPlan(
        start=stamp(start),
        end=stamp(end),
        hours=hours,
        forcing_endpoint_start=stamp(start + timedelta(hours=1)),
        forcing_endpoint_end=stamp(end),
        forcing_calendar_days=forcing_days,
        daily_product_days=product_days,
        input_restart_time=stamp(start),
        output_restart_time=stamp(end),
    )
