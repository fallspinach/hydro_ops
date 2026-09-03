import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

from hydro_ops.status_monitor import build_status, format_text, production_inventory


def touch(path: Path, size: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_production_inventory_reports_segments_gaps_and_partials(tmp_path):
    for stamp in ("20260101", "20260102", "20260104"):
        touch(tmp_path / stamp[:4] / stamp[4:6] / f"{stamp}.LDASIN_DOMAIN1", 3)
    touch(tmp_path / "2026/01/working.part")
    report = production_inventory(tmp_path, start=date(2026, 1, 1), end=date(2026, 1, 5))
    assert report["unique_days"] == 3
    assert report["bytes"] == 9
    assert report["missing_days"] == 2
    assert report["missing_day_examples"] == ["2026-01-03", "2026-01-05"]
    assert report["partial_files"] == 1
    assert [run["days"] for run in report["coverage_segments"]] == [2, 1]


def test_build_status_is_json_serializable_and_formats_text(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        project_root=tmp_path,
        output_root=tmp_path / "outputs",
        work_root=tmp_path / "work",
        nldas_data_dir=tmp_path / "nldas",
        stage4_data_dir=tmp_path / "stage4",
        prism_data_dir=tmp_path / "prism",
        prism_variables=("ppt",),
        hrrr_data_dir=tmp_path / "hrrr",
        mrms_data_dir=tmp_path / "mrms",
        mrms_products=("pass1", "pass2"),
    )
    touch(settings.hrrr_data_dir / "2026/01/01/hrrr_forcing.2026010100.grib2.nc")
    touch(settings.output_root / "forcing/nwm/nrt/2026/01/20260101.LDASIN_DOMAIN1", 7)
    monkeypatch.setattr(
        "hydro_ops.status_monitor.slurm_inventory",
        lambda: {"available": True, "jobs": [], "job_count": 0, "states": {}},
    )
    report = build_status(settings, now=datetime(2026, 1, 2, tzinfo=UTC))
    assert json.loads(json.dumps(report))["schema_version"] == "1.0"
    assert report["production_streams"]["nrt"]["bytes"] == 7
    assert "NWM daily production streams" in format_text(report)
