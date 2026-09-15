"""Profile a complete isolated repair day with its three baseline inputs."""
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    result = root/'forcing/work/post2020-stage-profile'/f'job_{job}'
    result.mkdir(parents=True, exist_ok=False)
    task = result/'task.jsonl'
    task.write_text(json.dumps({'start': '2021-09-04', 'end': '2021-09-04',
                               'stream': 'retro', 'revision': 'stable',
                               'output_root': str(result/'output')})+'\n')
    env = dict(os.environ)
    env.update(HYDRO_OPS_PROFILE_DIRECTORY=str(result/'profiles'),
               PYTHONPATH=str(root/'tools/forcing_profile')+os.pathsep+env.get('PYTHONPATH', ''),
               HYDRO_OPS_PROJECT_ROOT=str(root), HYDRO_OPS_PYTHON=sys.executable,
               HYDRO_OPS_REBUILD_TASK_FILE=str(task),
               HYDRO_OPS_REBUILD_STATIC_ENVELOPE='1',
               HYDRO_OPS_BENCH_MULTIDAY='0', HYDRO_OPS_BENCH_FAST_MASK='0',
               HYDRO_OPS_PRECIPITATION_REMAP_WORKERS='1', HYDRO_OPS_ASSEMBLY_WORKERS='4')
    completed = subprocess.run([sys.executable, str(root/'slurm/rebuild_post2020_forcing.py')], env=env, check=False)
    (result/'status.json').write_text(json.dumps({'status': 'passed' if completed.returncode == 0 else 'failed',
                                                'exit_code': completed.returncode,
                                                'scope': 'one-day isolated reference profile; instrumentation overhead included'}, indent=2)+'\n')
    return completed.returncode


if __name__ == '__main__':
    raise SystemExit(main())
