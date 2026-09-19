"""Immutable, dependency-addressed PRISM windows; never production forcing files."""
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class WindowCache:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self, exclusive=False):
        with (self.root / '.cache.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield

    def restore(self, key, destination, identity):
        with self.lock():
            return self._restore(key, destination, identity)

    def _restore(self, key, destination, identity):
        entry = self.root / key
        try:
            record = json.loads((entry / 'receipt.json').read_text())
            if record['identity'] != identity(entry / 'window.nc'):
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry / 'window.nc', destination)
            if checksum(destination) != record['sha256']:
                return False
            shutil.copyfile(entry / 'manifest.json', destination.with_name(destination.name + '.manifest.json'))
            os.utime(entry, None)
            return True
        except (OSError, ValueError, KeyError):
            return False

    def publish(self, key, source, identity):
        with self.lock():
            self._publish(key, source, identity)

    def _publish(self, key, source, identity):
        # Per-key locking permits independent workers; atomic directory publication
        # ensures no reader can see an incomplete window/manifest pair.
        with (self.root / (key + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entry = self.root / key
            if entry.exists():
                return
            with tempfile.TemporaryDirectory(dir=self.root, prefix='.stage-') as temp:
                staged = Path(temp) / 'entry'
                staged.mkdir()
                shutil.copyfile(source, staged / 'window.nc')
                shutil.copyfile(source.with_name(source.name + '.manifest.json'), staged / 'manifest.json')
                # Path changes at rename; inode, size and mtime are preserved.
                record = identity(staged / 'window.nc')
                record['path'] = str((entry / 'window.nc').resolve())
                digest = checksum(source)
                if checksum(staged / 'window.nc') != digest:
                    raise ValueError('Window cache transfer checksum differs')
                (staged / 'receipt.json').write_text(json.dumps({'identity': record, 'sha256': digest}))
                staged.rename(entry)

    def prune(self, *, max_entries=32, max_bytes=160_000_000_000, max_age_days=14):
        """Evict only recognized cache entries while no reader/publisher uses them."""
        if min(max_entries, max_bytes, max_age_days) <= 0:
            raise ValueError('Cache retention limits must be positive')
        removed = []
        with self.lock(exclusive=True):
            entries = []
            for entry in self.root.iterdir():
                if entry.is_symlink() or not entry.is_dir() or not re.fullmatch('[0-9a-f]{64}', entry.name):
                    continue
                children = list(entry.iterdir())
                if {p.name for p in children} != {'window.nc', 'manifest.json', 'receipt.json'}:
                    continue
                if any(p.is_symlink() or not p.is_file() for p in children):
                    continue
                entries.append((entry.stat().st_mtime, sum(p.stat().st_size for p in children), entry))
            entries.sort()
            total, count = sum(e[1] for e in entries), len(entries)
            for touched, size, entry in entries:
                if count <= max_entries and total <= max_bytes and touched >= time.time() - max_age_days * 86400:
                    continue
                # Exact validated directory only; never recursively traverse unknown content.
                for name in ('window.nc', 'manifest.json', 'receipt.json'):
                    (entry / name).unlink()
                entry.rmdir()
                (self.root / (entry.name + '.lock')).unlink(missing_ok=True)
                removed.append(entry.name)
                total -= size
                count -= 1
        return removed
