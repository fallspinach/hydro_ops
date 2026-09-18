import importlib.util
from datetime import date
from pathlib import Path

import pytest
from netCDF4 import Dataset
from test_static_forcing_mask_pilot import fixture_files


@pytest.mark.parametrize("optimized_first", [False, True])
def test_paired_hourly_assembly(tmp_path, monkeypatch, optimized_first):
    bindir = Path(__file__).parents[1] / "bin"
    monkeypatch.syspath_prepend(str(bindir))
    spec = importlib.util.spec_from_file_location("baseline_benchmark", bindir / "benchmark_nrt_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = []
    for hour in range(2):
        folder = tmp_path / str(hour)
        folder.mkdir()
        source, _ = fixture_files(folder, chunksizes=(1, 2, 2))
        with Dataset(source, "r+") as data:
            data["time"].units = "hours since 2000-01-01"
        paths.append(source)
    report = module.paired_archive(paths, tmp_path / "daily.nc", date(2000, 1, 1),
                                   expected_hours=2, source_time_indices=[0, 1],
                                   optimized_first=optimized_first,
                                   chunk_copy=True, preserve_source_chunks=True)
    assert report["status"] == "passed"
    assert report["arms"]["optimized"]["archive_writer"] == "compressed_chunks"
    assert report["order"][0] == ("optimized" if optimized_first else "reference")
