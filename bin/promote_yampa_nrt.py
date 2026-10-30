"""Audit and promote accepted Yampa/B120 archives using same-filesystem renames."""
import argparse
import fcntl
import json
import os
import subprocess
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from migrate_cnrfc_grid import archive_audit, lidentity, rewrite

from hydro_ops.download.cdec_fnf import atomic_bytes, write_json

CAMPAIGN = 'nrt_continuation_v1'


def promote(root, execute=False, domain='yampa', stream='nrt'):
    if domain not in ('yampa', 'b120') or stream not in ('nrt', 'retro'):
        raise ValueError('Unsupported promotion domain')
    campaign = CAMPAIGN if stream == 'nrt' else 'production_v1'
    state = root / f'nwm/status/{domain}/{stream}-promotion'
    state.mkdir(parents=True, exist_ok=True)
    marker = state / 'promotion.json'
    if marker.exists():
        raise RuntimeError('Existing promotion journal requires inspection')
    runs = root / f'nwm/runs/{domain}/{stream}' / campaign
    with ExitStack() as stack:
        locks = [runs.parent / 'production.lock'] if stream == 'nrt' else [
            runs / 'production.lock', root / f'nwm/runs/{domain}/nrt/production.lock']
        for path in locks:
            lock = stack.enter_context(path.open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        jobs = subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%j'], text=True)
        if any(domain in line.lower() and line.split('|', 1)[0] != os.environ.get('SLURM_JOB_ID')
               for line in jobs.splitlines()):
            raise RuntimeError(f'{domain} jobs still queued/running')
        gate_dir = root / ('nwm/status/subdomain-nrt-catchup/yampa' if domain == 'yampa'
                           else 'nwm/status/b120-nrt-catchup/b120')
        gate = json.loads((gate_dir / 'handoff-acceptance.json').read_text())
        if (gate.get('status') != 'passed' or gate.get('domain') != domain
                or not gate.get('nrt_handoff_verified') or not gate.get('all_archived_records_verified')):
            raise ValueError('Missing accepted warm-restart handoff')
        if stream == 'nrt':
            markers = list(runs.glob('*-passed.json'))
            if len(markers) != 1:
                raise ValueError('Expected exactly one accepted catch-up interval')
            interval = json.loads(markers[0].read_text())
            if not interval.get('passed'):
                raise ValueError('Campaign incomplete')
            start, end = (datetime.fromisoformat(interval[k]) for k in ('start', 'end'))
        else:
            start, end = map(datetime.fromisoformat, ('1979-01-02', '2026-04-18'))
            for year in range(1979, 2027):
                annual = json.loads((runs / f'{year}-passed.json').read_text())
                if not annual.get('passed') or annual.get('stream') != 'retro':
                    raise ValueError(f'Unaccepted annual campaign: {year}')
        print(json.dumps({'stage': 'auditing', 'domain': domain, 'stream': stream,
                          'start': str(start), 'end': str(end)}), flush=True)
        audit = archive_audit(root, domain, stream, campaign, start, end)
        print(json.dumps({'stage': 'audit_passed', **audit}), flush=True)
        output = root / f'nwm/outputs/{domain}/{stream}'
        restart = root / f'nwm/restarts/{domain}/{stream}'
        moves = [(output / campaign / kind, output / kind) for kind in ('hourly', 'daily')]
        years = sorted((restart / campaign).iterdir())
        if not years or any(not p.name.isdigit() or len(p.name) != 4 for p in years):
            raise ValueError('Unexpected restart contents')
        moves += [(p, restart / p.name) for p in years]
        files = []
        for source, target in moves:
            if (source.is_symlink() or not source.is_dir() or target.exists()
                    or target.is_symlink() or source.stat().st_dev != target.parent.stat().st_dev):
                raise ValueError(f'Unsafe/conflicting move: {source} -> {target}')
            for p in source.rglob('*'):
                if p.is_symlink():
                    raise ValueError(f'Unexpected symlink: {p}')
                if p.is_file():
                    files.append({'old': str(p), 'new': str(target / p.relative_to(source)),
                                  'identity': lidentity(p)})
        mapping = {f'nwm/{kind}/{domain}/{stream}/{campaign}': f'nwm/{kind}/{domain}/{stream}'
                   for kind in ('outputs', 'restarts')}
        plan = {'audit': audit, 'files': files, 'moves': [[str(a), str(b)] for a, b in moves]}
        write_json(state / 'plan.json', plan)
        print(json.dumps({'stage': 'move_plan_ready', 'files': len(files)}), flush=True)
        if not execute:
            return {'status': 'audited', **audit, 'files': len(files)}
        report = {'status': 'moving', 'domain': domain, 'stream': stream,
                  'audit': audit, 'completed_moves': [], 'metadata_updated': []}
        write_json(marker, report)
        for source, target in moves:
            source.rename(target)
            report['completed_moves'].append([str(source), str(target)])
            write_json(marker, report)
        for record in files:
            if lidentity(Path(record['new'])) != record['identity']:
                raise ValueError(f'Publication identity changed: {record["new"]}')
        # Preserve exact original metadata before rewriting current references.
        bases = [root / f'nwm/runs/{domain}' if stream == 'retro' else runs,
                 output / 'hourly', output / 'daily', *[restart / p.name for p in years],
                 gate_dir]
        for base in bases:
            for p in sorted(base.rglob('*.json')):
                original = p.read_text()
                updated = rewrite(original, mapping)
                if original == updated:
                    continue
                json.loads(updated)
                atomic_bytes(state / 'metadata-before' / p.relative_to(root), original.encode())
                atomic_bytes(p, updated.encode())
                report['metadata_updated'].append(str(p.relative_to(root)))
        after = archive_audit(root, domain, stream, campaign, start, end, promoted=True)
        if audit != after:
            raise ValueError('Post-promotion audit differs')
        for record in files:
            if not record['new'].endswith('.json') and lidentity(Path(record['new'])) != record['identity']:
                raise ValueError('Data identity changed')
        for container in (output / campaign, restart / campaign):
            container.rmdir()  # Only empty containers; never remove scientific data.
        report.update(status='complete', outputs=str(output), restarts=str(restart),
                      file_count=len(files), post_promotion_audit=after,
                      operational_hook_enabled=False)
        write_json(marker, report)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--domain', choices=('yampa', 'b120'), default='yampa')
    parser.add_argument('--stream', choices=('nrt', 'retro'), default='nrt')
    args = parser.parse_args()
    result = promote(Path(__file__).resolve().parents[1], args.execute, args.domain, args.stream)
    print(json.dumps({k: v for k, v in result.items() if k not in ('metadata_updated', 'completed_moves')}))
