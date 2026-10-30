import importlib
import json
from pathlib import Path


def test_requires_current_source_receipt(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    tool = importlib.import_module('backfill_b120_forcing')
    source = tmp_path / 'source'
    source.write_bytes(b'data')
    called = []
    monkeypatch.setattr(tool, 'process_retry', lambda task: called.append(task) or {'status': 'published'})
    task = (source,)
    assert tool.checked_process(task)['status'] == 'failed'
    receipt = source.with_name(source.name + '.subset.json')
    receipt.write_text(json.dumps({'status': 'passed', 'output_identity': tool.identity(source)}))
    assert tool.checked_process(task)['status'] == 'published'
    source.write_bytes(b'changed source')
    assert tool.checked_process(task)['status'] == 'failed'
    assert called == [task]
