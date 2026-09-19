import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from netCDF4 import Dataset

spec = importlib.util.spec_from_file_location('extension', Path(__file__).resolve().parents[1] / 'bin/benchmark_nrt_extension.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_input_gate_allows_artifact_hash_difference_but_not_source_changes():
    a = {'inputs': {'baseline_sha256': ['a'], 'baseline_fingerprints': ['source'], 'prism': ['same']}}
    b = {'inputs': {**a['inputs'], 'baseline_sha256': ['b']}}
    module.compare_inputs(a, b)
    b['inputs']['baseline_fingerprints'] = ['changed']
    with pytest.raises(ValueError, match='Source inputs'):
        module.compare_inputs(a, b)


def test_clone_rebases_only_publication_identity(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'private/copy'
    source.write_bytes(b'accepted forcing')
    record = {'status': 'passed', 'published_identity': module.cycle.identity(source),
              'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'input_fingerprint': 'inputs'}
    module.receipt(source).write_text(json.dumps(record))
    module.clone(source, destination)
    copied = json.loads(module.receipt(destination).read_text())
    assert copied == {**record, 'published_identity': module.cycle.identity(destination)}
    assert json.loads(module.receipt(source).read_text()) == record
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Stale'):
        module.clone(source, tmp_path / 'other')


def test_comparison_rejects_changed_values(tmp_path):
    paths = [tmp_path / 'a', tmp_path / 'b']
    for path in paths:
        with Dataset(path, 'w') as data:
            data.createDimension('time', 2)
            data.createVariable('T2D', 'f4', ('time',))[:] = [270, 280]
            for attr in ('forcing_stream', 'archive_granularity', 'forcing_domain_policy',
                         'prism_reconciliation_accepted', 'prism_precipitation_revisions'):
                data.setncattr(attr, 'test')
    module.compare(*paths)
    with Dataset(paths[1], 'r+') as data:
        data['T2D'][1] = 285
    with pytest.raises(AssertionError):
        module.compare(*paths)
