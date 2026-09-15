"""Raw compressed chunk copy is checked against independently masked values."""
import importlib.util
from pathlib import Path

import pytest
from netCDF4 import Dataset
from test_static_forcing_mask_pilot import fixture_files


@pytest.fixture
def chunk_tool(monkeypatch):
    directory = Path(__file__).parents[1] / 'bin'
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('chunk_benchmark', directory/'benchmark_static_mask_chunks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('chunks', [(1, 2, 2), (1, 1, 1)])
def test_exact_masking(tmp_path, chunk_tool, chunks):
    source, mask = fixture_files(tmp_path, chunksizes=chunks)
    output = tmp_path/'output.nc'
    before = source.read_bytes()
    result = chunk_tool.chunk_mask(source, mask, output)
    assert sum(result['chunks'].values()) > 0
    chunk_tool.verify(source, mask, output)
    chunk_tool.verify_chunks(source, mask, output)
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match='separate'):
        chunk_tool.chunk_mask(source, mask, output)


def test_missing_active_rejected_by_audit(tmp_path, chunk_tool):
    source, mask = fixture_files(tmp_path, chunksizes=(1, 2, 2))
    with Dataset(source, 'r+') as data:
        data['T2D'][0, 0, 0] = -9999
    output = tmp_path/'output.nc'
    chunk_tool.chunk_mask(source, mask, output)
    with pytest.raises(ValueError, match='missing active'):
        chunk_tool.verify(source, mask, output)


@pytest.mark.parametrize('chunks,cell,error', [
    ((1, 1, 1), (0, 0, 0), 'Copied chunk'),
    ((1, 1, 1), (0, 1, 0), 'Excluded chunk'),
    ((1, 2, 2), (0, 0, 0), 'Boundary chunk'),
])
def test_integrity_detects_corruption(tmp_path, chunk_tool, chunks, cell, error):
    source, mask = fixture_files(tmp_path, chunksizes=chunks)
    output = tmp_path/'output.nc'
    chunk_tool.chunk_mask(source, mask, output)
    with Dataset(output, 'r+') as data:
        data['T2D'][cell] = 123
    with pytest.raises(ValueError, match=error):
        chunk_tool.verify_chunks(source, mask, output)
