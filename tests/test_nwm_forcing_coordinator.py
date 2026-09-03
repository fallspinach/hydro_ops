import importlib.util
from datetime import date
from pathlib import Path


def load_script(name: str):
    path = Path(__file__).parents[1] / "bin" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cycle_windows_are_nonoverlapping_at_daily_slot() -> None:
    module = load_script("update_nwm_forcing.py")
    today = date(2026, 9, 3)
    assert module.cycle_window("six-hourly", today)[1:3] == (
        date(2026, 8, 23),
        date(2026, 9, 1),
    )
    assert module.cycle_window("daily", today)[1:3] == (
        date(2026, 2, 14),
        date(2026, 9, 1),
    )
    assert module.cycle_window("monthly-retro", today)[1:3] == (
        date(2026, 1, 19),
        date(2026, 3, 4),
    )


def test_prism_revision_routing() -> None:
    module = load_script("submit_prism_calendar_batches.py")
    today = date(2026, 9, 3)
    assert module.revision_for_day(date(2026, 9, 1), today, "nrt") == "early"
    assert module.revision_for_day(date(2026, 8, 1), today, "nrt") == "provisional"
    assert module.revision_for_day(date(2026, 3, 4), today, "nrt") is None
    assert module.revision_for_day(date(2026, 3, 4), today, "retro") == "stable"


def test_batches_split_at_revision_boundary() -> None:
    module = load_script("submit_prism_calendar_batches.py")
    days = [
        (date(2026, 8, 31), "provisional"),
        (date(2026, 9, 1), "early"),
        (date(2026, 9, 2), "early"),
    ]
    assert module.contiguous_batches(days, 31) == [
        (date(2026, 8, 31), date(2026, 8, 31), "provisional"),
        (date(2026, 9, 1), date(2026, 9, 2), "early"),
    ]
