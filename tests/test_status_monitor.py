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
    touch(settings.output_root / "conus/nrt/hourly/2026/01/20260101.LDASIN_DOMAIN1", 7)
    touch(settings.output_root / "cnrfc/retro/daily/2026/01/20260101.LDASIN_DOMAIN1.daily", 4)
    touch(settings.output_root / "cnrfc/retro/monthly/2026/202601.LDASIN_DOMAIN1.monthly", 6)
    status = tmp_path / "forcing/status/nrt-summaries/latest.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "failed", "job_id": "123"}))
    monkeypatch.setattr(
        "hydro_ops.status_monitor.slurm_inventory",
        lambda: {"available": True, "jobs": [], "job_count": 0, "states": {}},
    )
    report = build_status(settings, now=datetime(2026, 1, 2, tzinfo=UTC))
    assert json.loads(json.dumps(report))["schema_version"] == "1.1"
    assert report["production_streams"]["nrt"]["bytes"] == 7
    assert "NWM hourly production streams" in format_text(report)
    assert "conus/nrt/monthly" in format_text(report)
    assert report["scan"]["summary_freshness_validated"] is False
    assert report["summary_streams"]["cnrfc"]["retro"]["daily"]["bytes"] == 4
    assert report["summary_streams"]["cnrfc"]["retro"]["monthly"]["unique_months"] == 1
    assert "NRT summary refresh: failed; job=123" in format_text(report)
    assert any("summary refresh failed" in issue for issue in report["summary"]["issues"])


def test_summary_period_inventory(tmp_path):
    for stamp in ("202512", "202602", "202603"):
        touch(tmp_path / stamp[:4] / f"{stamp}.LDASIN_DOMAIN1.monthly", 5)
    touch(tmp_path / "2026/202602.LDASIN_DOMAIN1.monthly.part")
    touch(tmp_path / "2026/202613.LDASIN_DOMAIN1.monthly")
    report = production_inventory(tmp_path, frequency="monthly", gap_limit=0)
    assert report["first_month"] == "2025-12"
    assert report["last_month"] == "2026-03"
    assert report["unique_months"] == 3
    assert report["missing_months"] == 1
    assert report["missing_months_truncated"] is True
    assert report["missing_month_examples"] == []
    assert report["partial_files"] == 1
    assert report["bytes"] == 15
    report = production_inventory(
        tmp_path, frequency="monthly", start=date(2026, 1, 15), end=date(2026, 2, 28)
    )
    assert report["missing_month_examples"] == ["2026-01"]
    assert report["unique_months"] == 1
    touch(tmp_path / "2026/01/20260101.LDASIN_DOMAIN1.daily", 2)
    report = production_inventory(tmp_path, frequency="daily", start=date(2026, 1, 1),
                                  end=date(2026, 1, 3))
    assert report["unique_days"] == 1
    assert report["missing_days"] == 2
    assert report["bytes"] == 2


def test_empty_summary_window(tmp_path):
    report = production_inventory(tmp_path, frequency="monthly", start=date(2024, 2, 1),
                                  end=date(2024, 3, 31))
    assert report["missing_months"] == 2
    assert report["files"] == 0
