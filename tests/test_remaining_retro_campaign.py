import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "remaining", Path(__file__).resolve().parents[1] / "bin/submit_remaining_retro_forcing.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_remaining_blocks_contiguous_and_bounded():
    last = date(2002, 12, 31)
    count = 0
    for start, end in module.blocks():
        start, end = date.fromisoformat(start), date.fromisoformat(end)
        assert start == last + timedelta(days=1)
        count += (end - start).days + 1
        last = end
    assert last == date(2020, 10, 13)
    assert count == 6496


def test_gate_requires_both_full_months(tmp_path):
    for month in ("01", "07"):
        report = {"status": "passed", "start": f"2003-{month}-01",
                  "end": f"2003-{month}-31", "days": 31}
        (tmp_path / f"benchmark-2003{month}.accepted.json").write_text(json.dumps(report))
    module.accept(tmp_path)
    assert json.loads((tmp_path / "gate.json").read_text())["status"] == "passed"
    (tmp_path / "gate.json").unlink()
    report["days"] = 30
    (tmp_path / "benchmark-200307.accepted.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Incomplete"):
        module.accept(tmp_path)
    assert not (tmp_path / "gate.json").exists()


def test_retry_preserves_failed_states_and_rewires_only_gate(tmp_path, monkeypatch):
    records = [{"job": "1", "command": ["sbatch", "--job-name=jan"]},
               {"job": "2", "command": ["sbatch", "--job-name=jul"]}, {"job": "3"}]
    (tmp_path / "submission.json").write_text(json.dumps(records))
    for month in ("200301", "200307"):
        (tmp_path / f"benchmark-{month}.json").write_text(json.dumps({
            "status": "blocked_after_maximum_attempts", "baseline_job_ids": ["old"]}))
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        return "" if command[0] == "squeue" else str(100 + len(calls))

    monkeypatch.setattr(module.subprocess, "check_output", output)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append(command))
    module.retry_failed(tmp_path)
    assert calls[-1] == ["scontrol", "update", "JobId=3", "Dependency=afterok:102:103"]
    for month in ("200301", "200307"):
        assert json.loads((tmp_path / f"benchmark-{month}.initial-failed.json").read_text())["baseline_job_ids"] == ["old"]


def test_campaign_rejects_prism_only_skips(monkeypatch, tmp_path):
    import runpy

    from hydro_ops.forcing import retro_publication

    monkeypatch.setenv("HYDRO_OPS_RETRO_NEW_PRODUCTION", "1")
    monkeypatch.setattr(retro_publication, "complete", lambda path: False)
    project = Path(__file__).resolve().parents[1]
    converge = runpy.run_path(str(project / "slurm/converge_nwm_forcing_cycle.py"))
    submit = runpy.run_path(str(project / "bin/submit_prism_calendar_batches.py"))
    assert not converge["accepted_output"](tmp_path / "partial.nc", "retro")
    assert not submit["valid_output"](tmp_path / "partial.nc", date(2003, 1, 1))
    state = {"start": "2003-01-01", "end": "2003-01-31", "stream": "retro",
             "prism_concurrency": 16, "scratch_mb": 240000}
    assert "240000" in converge["prism_command"]("python", project, state)


def test_scratch_preflight_fails_before_work(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from hydro_ops.forcing import retro_publication

    monkeypatch.setenv("HYDRO_OPS_MIN_SCRATCH_FREE_GB", "120")
    monkeypatch.setattr(retro_publication.shutil, "disk_usage", lambda path: SimpleNamespace(free=119 * 10**9))
    with pytest.raises(RuntimeError, match="Insufficient scratch"):
        retro_publication.check_scratch(tmp_path)


def test_validated_writers_selected_at_submission_for_existing_controllers():
    import runpy

    path = Path(__file__).resolve().parents[1] / 'bin/submit_prism_calendar_batches.py'
    exports = runpy.run_path(str(path))['writer_exports']
    env = {'HYDRO_OPS_RETRO_NEW_PRODUCTION': '1'}
    result = exports('retro', env)
    assert result == {'HYDRO_OPS_RETRO_WRITER_PROFILE': 'validated_chunks_v1',
                      'HYDRO_OPS_ARCHIVE_CHUNKS': '1', 'HYDRO_OPS_BENCH_FAST_MASK': '1'}
    assert env == {'HYDRO_OPS_RETRO_NEW_PRODUCTION': '1'}
    assert exports('nrt', env) == {}
    assert exports('retro', {}) == {}
    reference = exports('retro', {**env, 'HYDRO_OPS_RETRO_WRITER_PROFILE': 'reference'})
    assert reference['HYDRO_OPS_ARCHIVE_CHUNKS'] == '0'
    assert reference['HYDRO_OPS_BENCH_FAST_MASK'] == '0'
    with pytest.raises(ValueError, match='Unknown retro writer profile'):
        exports('retro', {**env, 'HYDRO_OPS_RETRO_WRITER_PROFILE': 'typo'})
