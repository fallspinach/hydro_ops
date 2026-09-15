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
