"""Profiling is opt-in and includes subprocess timings in isolated interpreters."""
import json
import os
import subprocess
import sys
from pathlib import Path


def test_profile_parent_and_child(tmp_path):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).parents[1]/'tools/forcing_profile')
    env['HYDRO_OPS_PROFILE_DIRECTORY'] = str(tmp_path)
    subprocess.run([sys.executable, '-c',
                    'import subprocess,sys; subprocess.run([sys.executable,"-c","sum(range(100))"],check=True)'],
                   env=env, check=True)
    reports = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
    assert len(reports) == 2
    assert any(r['subprocesses'] for r in reports)
    assert all(r['top_self'] for r in reports)


def test_timing_only_has_no_function_profiler(tmp_path):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).parents[1]/'tools/forcing_profile')
    env['HYDRO_OPS_PROFILE_DIRECTORY'] = str(tmp_path)
    env['HYDRO_OPS_PROFILE_TIMING_ONLY'] = '1'
    subprocess.run([sys.executable, '-c',
                    'import subprocess,sys; subprocess.run([sys.executable,"-c","pass"],check=True)'],
                   env=env, check=True)
    reports = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
    assert len(reports) == 2
    assert not list(tmp_path.glob('*.pstats'))
    assert all(r['mode'] == 'timing_only' and not r['top_self'] for r in reports)
    assert any(r['subprocesses'] for r in reports)


def test_all_post2020_optimizations_exclude_remap_experiment():
    import runpy

    flags = runpy.run_path(str(Path(__file__).parents[1]/'bin/benchmark_post2020_production.py'))['optimization_flags']
    assert set(flags('reference', all_optimizations=True).values()) == {'0'}
    optimized = flags('optimized', all_optimizations=True)
    assert optimized['HYDRO_OPS_BENCH_MULTIDAY'] == '0'
    assert optimized['HYDRO_OPS_ARCHIVE_CHUNKS'] == '1'
    assert optimized['HYDRO_OPS_BENCH_FAST_MASK'] == '1'
    assert flags('optimized', chunk_archives=True)['HYDRO_OPS_BENCH_FAST_MASK'] == '0'
