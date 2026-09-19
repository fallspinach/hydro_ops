import json
import os
import time

from hydro_ops.forcing.nrt_cycle import identity
from hydro_ops.forcing.window_cache import WindowCache


def test_cache_restore_miss_and_corruption(tmp_path):
    cache = WindowCache(tmp_path / 'cache')
    source, target = tmp_path / 'source.nc', tmp_path / 'scratch/window.nc'
    source.write_bytes(b'accepted window')
    source.with_name(source.name + '.manifest.json').write_text(json.dumps({'verified': True}))
    assert not cache.restore('missing', target, identity)
    cache.publish('key', source, identity)
    assert cache.restore('key', target, identity)
    assert target.read_bytes() == source.read_bytes()
    assert not cache.restore('new-dependencies', target, identity)
    (tmp_path / 'cache/key/window.nc').write_bytes(b'corrupt')
    assert not cache.restore('key', target, identity)


def test_publish_is_immutable(tmp_path):
    cache = WindowCache(tmp_path / 'cache')
    source = tmp_path / 'source'
    source.write_bytes(b'first')
    source.with_name(source.name + '.manifest.json').write_text('{}')
    cache.publish('key', source, identity)
    source.write_bytes(b'second')
    cache.publish('key', source, identity)
    assert (tmp_path / 'cache/key/window.nc').read_bytes() == b'first'


def test_prune_bounds_and_preserves_unknown_content(tmp_path):
    cache = WindowCache(tmp_path / 'cache')
    source = tmp_path / 'source'
    source.write_bytes(b'window')
    source.with_name(source.name + '.manifest.json').write_text('{}')
    for i in range(3):
        key = f'{i:064x}'
        cache.publish(key, source, identity)
        os.utime(cache.root / key, (time.time() - 100 + i, time.time() - 100 + i))
    unknown = cache.root / 'unrelated'
    unknown.mkdir()
    (unknown / 'keep').write_text('user data')
    assert len(cache.prune(max_entries=1)) == 2
    assert (cache.root / f'{2:064x}').is_dir()
    assert (unknown / 'keep').read_text() == 'user data'
    assert len(cache.prune(max_bytes=1)) == 1


def test_expired_cache_is_removed(tmp_path):
    cache = WindowCache(tmp_path / 'cache')
    source = tmp_path / 'source'
    source.write_bytes(b'window')
    source.with_name(source.name + '.manifest.json').write_text('{}')
    key = 'a' * 64
    cache.publish(key, source, identity)
    os.utime(cache.root / key, (1, 1))
    assert cache.prune(max_age_days=14) == [key]
