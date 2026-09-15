"""Read-only planning and latency accounting for incremental forcing operations.

Plans describe required work, not permission to skip scientific dependencies.
The existing production coordinator is not switched by this module.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

TARGET_SECONDS = 1800
DEADLINE_SECONDS = 3600
KNOWN_CHANGES = {"new_hours", "stage4", "mrms", "prism_ppt", "prism_temperature",
                 "nldas2", "hrrr", "gfs", "missing_output", "policy"}


def utc(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError("Operational timestamps must include a UTC offset")
    return value.astimezone(UTC)


def latency(requested, worker_started, finished):
    requested, worker_started, finished = map(utc, (requested, worker_started, finished))
    if not requested <= worker_started <= finished:
        raise ValueError("Invalid cycle timestamp ordering")
    total = (finished - requested).total_seconds()
    return {
        "launch_to_publication_seconds": total,
        "pre_worker_seconds": (worker_started - requested).total_seconds(),
        "worker_seconds": (finished - worker_started).total_seconds(),
        "target_seconds": TARGET_SECONDS, "deadline_seconds": DEADLINE_SECONDS,
        "latency_status": "within_target" if total <= TARGET_SECONDS else
                          "over_target" if total < DEADLINE_SECONDS else "deadline_missed",
        "note": "Pre-worker time includes refresh, dependencies and queueing; not queue time alone",
    }


def work_for(changes):
    changes = set(changes)
    if changes - KNOWN_CHANGES:
        raise ValueError(f"Unknown changes: {sorted(changes - KNOWN_CHANGES)}")
    if not changes:
        return []
    full = bool(changes & {"new_hours", "missing_output", "policy", "nldas2", "hrrr", "gfs"})
    precipitation = full or bool(changes & {"stage4", "mrms"})
    tasks = []
    if full:
        tasks.append("meteorology_selection_remap_and_coupled_processing")
    if precipitation:
        tasks += ["precipitation_selection_remap_and_cnrf_constraint"]
    if precipitation or "prism_ppt" in changes:
        tasks += ["precipitation_prism_reconciliation_if_eligible"]
    if full or "prism_temperature" in changes:
        tasks += ["temperature_prism_and_humidity_longwave_if_eligible"]
    return tasks + ["combined_domain_mask_validation_and_calendar_publication"]


def plan(events, as_of, *, lane="fast", recent_days=4):
    """Classify source-change events into fast NRT or deferred work.

    Events are pre-discovered changes, NOT inferred from a file's mtime alone.
    Missing/new NRT data are prioritized even when older than the recent tail.
    PRISM availability never removes the need for subdaily precipitation inputs.
    """
    as_of = utc(as_of)
    if lane not in {"fast", "daily-nrt", "daily-retro"} or recent_days < 1:
        raise ValueError("Invalid lane or recent lookback")
    work, deferred, unchanged = [], [], []
    for event in events:
        day = datetime.strptime(event["day"], "%Y-%m-%d").replace(tzinfo=UTC)
        changes = set(event.get("changes", []))
        tasks = work_for(changes)
        stream = event.get("stream", "nrt")
        if stream not in {"nrt", "retro"}:
            raise ValueError("Stream must be nrt or retro")
        if day.date() > as_of.date():
            raise ValueError("Future target day")
        item = {**event, "changes": sorted(changes), "tasks": tasks}
        if not tasks:
            unchanged.append(item)
            continue
        urgent = bool(changes & {"new_hours", "missing_output"})
        recent = day.date() >= (as_of - timedelta(days=recent_days - 1)).date()
        if stream == "retro":
            eligible = lane == "daily-retro" and event.get("prism_stable") is True
            destination_lane = "daily-retro"
        else:
            eligible = lane == "daily-nrt" or (lane == "fast" and (urgent or recent))
            destination_lane = "fast" if urgent or recent else "daily-nrt"
        item["priority"] = 0 if urgent else 1
        if eligible:
            work.append(item)
        else:
            item["deferred_to"] = destination_lane
            deferred.append(item)
    work.sort(key=lambda item: (item["priority"], -int(item["day"].replace("-", ""))))
    return {"schema": "incremental_forcing_plan_v1", "as_of": as_of.isoformat(), "lane": lane,
            "execution_enabled": False, "target_seconds": TARGET_SECONDS,
            "deadline_seconds": DEADLINE_SECONDS, "work": work, "deferred": deferred,
            "unchanged": unchanged,
            "publication_rule": "verified complete requested interval only; retain prior files on failure",
            "halo_rule": "resolve shared PRISM 12-12 windows and Stage-IV six-hour blocks once; publish 00-23 chunks"}
