"""Journaled, exact-inventory cutover; no data copying, deletion or recompression."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from hydro_ops.forcing.layout_migration import map_dated_forcing_path


def remap(value, root):
    if isinstance(value, str):
        return map_dated_forcing_path(value, root)
    if isinstance(value, list):
        return [remap(v, root) for v in value]
    if isinstance(value, dict):
        return {remap(k, root): remap(v, root) for k, v in value.items()}
    return value


def identity(path):
    stat = path.stat()
    return {"inode": stat.st_ino, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def validated_moves(plan, root):
    if plan['conflicts'] or Path(plan['project_root']).resolve() != root.resolve():
        raise ValueError('Conflicting inventory or wrong project')
    moves = []
    for item in plan['proposed_moves']:
        relative = Path(item['source'])
        parts = relative.parts
        if (relative.is_absolute() or len(parts) != 5 or parts[:2] != ('forcing', 'outputs')
                or parts[3] not in {'baseline', 'nrt', 'retro'} or len(parts[4]) != 4
                or not parts[4].isdigit() or '..' in parts):
            raise ValueError(f'Unsafe move: {relative}')
        if item['destination'] != map_dated_forcing_path(str(relative), root):
            raise ValueError('Destination does not match approved mapping')
        source, target = root / relative, root / item['destination']
        if any(p.is_symlink() for p in [source, *source.parents, target, *target.parents]):
            raise ValueError('Symlinks are not supported')
        if not source.is_dir() or target.exists():
            raise ValueError(f'Unexpected source/destination: {source}, {target}')
        moves.append((source, target))
    for item in plan['file_inventory']:
        path = root / item['source']
        if not any(path.is_relative_to(source) for source, _ in moves):
            raise ValueError(f'File outside moves: {path}')
        expected = map_dated_forcing_path(item['source'], root)
        if item['destination'] != expected or identity(path) != item['identity']:
            raise ValueError(f'Changed file: {path}')
    actual = {str(p.relative_to(root)) for source, _ in moves for p in source.rglob('*') if p.is_file()}
    if actual != {item['source'] for item in plan['file_inventory']}:
        raise ValueError('Inventory no longer complete')
    return moves


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--gate', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    plan = json.loads(args.inventory.read_text())
    root = Path(plan['project_root']).resolve()
    gate = json.loads(args.gate.read_text())
    if gate.get('status') != 'passed' or gate.get('file_identities_unchanged') is not True:
        raise ValueError('Operational acceptance gate has not passed')
    queue = subprocess.check_output(['squeue', '-h', '-u', os.environ['USER'], '-o', '%i|%T|%r'], text=True)
    if any(line.split('|')[1] != 'PENDING' for line in queue.splitlines()):
        raise ValueError('Active jobs block migration')
    if '4524571|PENDING|JobHeldUser' not in queue:
        raise ValueError('NWM 1986 hold is not confirmed')
    moves = validated_moves(plan, root)
    print(json.dumps({'moves': len(moves), 'files': len(plan['file_inventory']), 'execute': args.execute}), flush=True)
    if not args.execute:
        return
    args.state.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.inventory, args.state / 'inventory.json')
    shutil.copy2(args.gate, args.state / 'operational-acceptance.json')
    with (args.state / 'journal.jsonl').open('x') as journal:
        def record(payload):
            journal.write(json.dumps(payload) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.stat().st_dev != target.parent.stat().st_dev:
                raise ValueError('Cross-filesystem rename refused')
            record({'action': 'rename-intent', 'source': str(source), 'destination': str(target)})
            source.rename(target)
            record({'action': 'rename-complete', 'source': str(source), 'destination': str(target)})
        # Verify every byte-count, inode and mtime before modifying any sidecar.
        for item in plan['file_inventory']:
            if identity(root / item['destination']) != item['identity']:
                raise ValueError(f'Renamed identity differs: {item["destination"]}')
        record({'action': 'all-renamed-identities-passed', 'files': len(plan['file_inventory'])})
        rewritten = 0
        for item in plan['file_inventory']:
            path = root / item['destination']
            if path.suffix != '.json':
                continue
            value = json.loads(path.read_text())
            mapped = remap(value, root)
            if mapped == value:
                continue
            backup = args.state / 'metadata-before' / item['source']
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            record({'action': 'metadata-intent', 'path': str(path), 'backup': str(backup)})
            temporary = path.with_name(path.name + '.layout-part')
            with temporary.open('x') as handle:
                json.dump(mapped, handle, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            record({'action': 'metadata-complete', 'path': str(path)})
            rewritten += 1
        data_files = [v for v in plan['file_inventory'] if Path(v['destination']).suffix != '.json']
        for item in data_files:
            if identity(root / item['destination']) != item['identity']:
                raise ValueError(f'Data identity differs: {item["destination"]}')
        report = {'status': 'passed', 'renamed_directories': len(moves),
                  'files': len(plan['file_inventory']), 'metadata_rewritten': rewritten,
                  'non_json_identities_preserved': len(data_files), 'bytes': plan['total_bytes']}
        record({'action': 'completed', **report})
        (args.state / 'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
