import importlib.util
import json
from datetime import date
from pathlib import Path


def load_script(name: str):
    path = Path(__file__).parents[1] / "bin" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_slurm_script(name: str):
    path = Path(__file__).parents[1] / "slurm" / name
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


def test_convergence_baseline_command_includes_three_day_halo(tmp_path: Path) -> None:
    module = load_slurm_script("converge_nwm_forcing_cycle.py")
    state = {"start": "1983-01-01", "end": "1983-12-31"}
    command = module.baseline_command(
        "python", tmp_path, [date(1983, 1, 1), date(1983, 1, 3)], state
    )
    only = command.index("--only-days")
    missing = command.index("--missing-only")
    assert command[only + 1 : missing] == [
        "1982-12-31",
        "1983-01-01",
        "1983-01-02",
        "1983-01-03",
        "1983-01-04",
    ]


def test_convergence_accepts_calendar_stable_output(tmp_path: Path) -> None:
    module = load_slurm_script("converge_nwm_forcing_cycle.py")
    path = module.forcing_path(tmp_path, date(1983, 1, 1))
    path.parent.mkdir(parents=True)
    from netCDF4 import Dataset

    with Dataset(path, "w") as data:
        data.createDimension("time", 24)
        data.archive_granularity = "utc_calendar_day"
        data.prism_reconciliation_accepted = "true"
        data.prism_precipitation_revisions = json.dumps(
            {"1983-01-01": "stable", "1983-01-02": "stable"}
        )
    assert module.accepted_output(path, "retro")


def test_multi_year_shards_respect_slurm_array_limit() -> None:
    module = load_script("update_nwm_forcing_multi_year.py")
    ranges = module.shard_ranges(date(1982, 12, 31), date(1987, 1, 1))
    assert ranges == [
        (date(1982, 12, 31), date(1985, 9, 26)),
        (date(1985, 9, 27), date(1987, 1, 1)),
    ]
    assert module.allocate_workers(ranges, 42) == [29, 13]


def test_staged_multi_year_baseline_command() -> None:
    module = load_slurm_script("submit_nwm_forcing_multi_year.py")
    state = {
        "start": "1987-01-01",
        "end": "1994-12-31",
        "baseline_shards": [{"start": "1986-12-31", "end": "1989-09-26", "workers": 15}],
    }
    command = module.baseline_command("python", state, 0)
    assert command[command.index("--max-concurrent") + 1] == "15"
    assert command[command.index("--job-name") + 1] == ("nwm-retro-1987-1994-baseline-shard-1")


def test_staged_convergence_depends_on_every_submitted_shard(tmp_path: Path) -> None:
    module = load_slurm_script("submit_nwm_forcing_multi_year.py")
    state = {
        "start": "1987-01-01",
        "end": "1994-12-31",
        "partition": "shared-128",
        "account": "",
        "baseline_job_ids": ["101", "102", "103"],
    }
    command = module.convergence_command(tmp_path, "python", tmp_path / "manifest.json", state)
    assert "--dependency=afterany:101:102:103" in command
