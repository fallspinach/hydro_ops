"""Opt-in interpreter profiling, enabled only by the isolated profiling job.

This directory must explicitly be placed on PYTHONPATH. No production defaults
load it. Profiles include instrumentation overhead and are not benchmark times.
"""
import atexit
import cProfile
import json
import os
import pstats
import subprocess
import sys
import time
from pathlib import Path


def activate():
    location = os.environ.get('HYDRO_OPS_PROFILE_DIRECTORY')
    if not location:
        return
    destination = Path(location)
    destination.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    timing_only = os.environ.get('HYDRO_OPS_PROFILE_TIMING_ONLY') == '1'
    profile = None if timing_only else cProfile.Profile()
    calls = []
    original = subprocess.run

    def timed_run(*args, **kwargs):
        begin = time.perf_counter()
        command = args[0] if args else kwargs.get('args')
        try:
            result = original(*args, **kwargs)
            code = result.returncode
            return result
        except BaseException:
            code = 'exception'
            raise
        finally:
            calls.append({'command': str(command), 'wall_seconds': time.perf_counter()-begin,
                          'returncode': code})

    subprocess.run = timed_run
    if profile is not None:
        profile.enable()

    def finish():
        pid = os.getpid()
        rows = []
        if profile is not None:
            profile.disable()
            profile.dump_stats(str(destination/f'{pid}.pstats'))
            stats = pstats.Stats(profile)
            for (filename, line, function), (primitive, total, own, cumulative, callers) in stats.stats.items():
                rows.append({'file': filename, 'line': line, 'function': function,
                             'calls': total, 'self_seconds': own, 'cumulative_seconds': cumulative})
        report = {'pid': pid, 'argv': sys.argv, 'wall_seconds': time.perf_counter()-started,
                  'mode': 'timing_only' if timing_only else 'cprofile',
                  'subprocesses': calls,
                  'top_cumulative': sorted(rows, key=lambda r: r['cumulative_seconds'], reverse=True)[:100],
                  'top_self': sorted(rows, key=lambda r: r['self_seconds'], reverse=True)[:100]}
        (destination/f'{pid}.json').write_text(json.dumps(report, indent=2)+'\n')
    atexit.register(finish)


activate()
