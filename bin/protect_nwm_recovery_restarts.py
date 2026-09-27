"""Retain audited restart inodes against path deletion or atomic replacement.

Hard links are not an independent backup against disk failure or in-place writes.
No restart contents or permissions are modified.
"""
import json
import os
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    recovery = root / 'nwm/recovery/chrtout_record_dimension'
    audit = json.loads((recovery / 'audit-restarts.json').read_text())
    records = []
    for pair in audit['restarts']:
        for kind in ('land', 'hydro'):
            source = Path(pair[kind])
            if source.stat().st_size != pair[kind + '_bytes'] or source.stat().st_mtime_ns != pair[kind + '_mtime_ns']:
                raise ValueError(f'Restart changed since audit: {source}')
            target = recovery / 'protected_restarts' / source.relative_to(root / 'nwm')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not os.path.samefile(source, target):
                    raise ValueError(f'Conflicting preservation path: {target}')
            else:
                os.link(source, target)
            records.append({'source': str(source), 'retained_path': str(target),
                            'bytes': source.stat().st_size, 'time': pair['time']})
    result = {'status': 'passed', 'files': len(records), 'records': records,
              'policy': 'retain all; hard links protect deletion/replacement, not in-place changes'}
    partial = recovery / 'restart-retention.json.part'
    partial.write_text(json.dumps(result, indent=2) + '\n')
    partial.replace(recovery / 'restart-retention.json')
    print(json.dumps({'status': 'passed', 'retained_files': len(records)}))


if __name__ == '__main__':
    main()
