import importlib
from pathlib import Path


def test_publish_skip_and_changed_source(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_cnrfc_forcing')
    source, target, mask = [tmp_path / name for name in ('source', 'target', 'mask')]
    source.write_bytes(b'first')
    mask.write_bytes(b'mask')

    def subset(src, dst, masks):
        dst.write_bytes(src.read_bytes())
        return {'status': 'passed', 'source': str(src.resolve())}

    monkeypatch.setattr(tool, 'subset', subset)
    task = (source, target, mask, tool.digest(mask), tmp_path, tool.identity(source))
    assert tool.process(task)['status'] == 'published'
    assert tool.process(task)['status'] == 'skipped'
    source.write_bytes(b'updated source')
    assert tool.process(task)['status'] == 'failed'
    task = (*task[:-1], tool.identity(source))
    assert tool.process(task)['status'] == 'published'
    assert target.read_bytes() == b'updated source'
    target.write_bytes(b'corrupted output')
    assert tool.process(task)['status'] == 'published'
    mask.write_bytes(b'changed mask')
    # Change source to exercise the publication mask guard as well.
    source.write_bytes(b'next')
    assert tool.process((*task[:-1], tool.identity(source)))['status'] == 'failed'


def test_discovery_excludes_sidecars_and_baseline(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_cnrfc_forcing')
    for relative in ('retro/hourly/1981/01/19810101.LDASIN_DOMAIN1',
                     'nrt/daily/2026/05/20260501.LDASIN_DOMAIN1.daily',
                     'nrt/monthly/2026/202605.LDASIN_DOMAIN1.monthly',
                     'retro/hourly/1981/01/19810101.LDASIN_DOMAIN1.subset.json',
                     'baseline/hourly/1981/01/19810101.LDASIN_DOMAIN1'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert len(tool.discover(tmp_path)) == 3
