"""Latest-hour planning contract; does not certify or publish model-ready data.

Acquisition and publication must finish before a planned hour can be advertised
as model ready. Missing preferred products never block an available fallback.
"""
from datetime import timedelta


def plan_hours(start, as_of, select_source, require_gfs):
    """Find contiguous source-ready hours, stopping at the first coverage gap.

    Callbacks validate exact-hour primary fields and the required northern GFS
    bundle. Only FileNotFoundError means unavailable; other errors propagate.
    Neither PRISM nor MRMS pass 2 is a prerequisite. Cell-level coverage still
    requires the normal production audit. Both endpoints must be UTC-aware.
    """
    for value in (start, as_of):
        if value.utcoffset() != timedelta(0):
            raise ValueError("Expected UTC-aware timestamps")
    if start.minute or start.second or start.microsecond or start > as_of:
        raise ValueError("Invalid first hour")
    hours = []
    timestamp = start
    blocked = None
    while timestamp <= as_of:
        try:
            source = select_source(timestamp)
            if source.valid_time != timestamp:
                raise ValueError("Primary source has the wrong valid time")
            if source.product == "hrrr":
                require_gfs(timestamp)
            elif source.product != "nldas2":
                raise ValueError("Unsupported primary source")
        except FileNotFoundError as error:
            blocked = {"hour": timestamp.isoformat(), "reason": str(error)}
            break
        hours.append({"hour": timestamp.isoformat(), "primary": source.product})
        timestamp += timedelta(hours=1)
    return {"status": "planned_not_published", "hours": hours,
            "latest_source_ready_hour": hours[-1]["hour"] if hours else None,
            "blocked": blocked}


def publication_order(start, end, accepted_days):
    """Extend forward first; subsequently inspect recent revisions newest first."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    accepted = set(accepted_days)
    return [d for d in days if d not in accepted] + [d for d in reversed(days) if d in accepted]
