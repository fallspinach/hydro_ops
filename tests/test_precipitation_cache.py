"""Job-local caches must reject changed inputs and outputs."""
import json
from datetime import UTC, datetime

import pytest

from hydro_ops.forcing.precipitation_cache import assets, identity, records, reuse


def test_cache_identity_guard(tmp_path):
    source = tmp_path/'source'
    source.write_bytes(b'source')
    output = tmp_path/'output'
    output.write_bytes(b'output')
    times = [datetime(2021, 9, 1, tzinfo=UTC)]
    candidates = [{'nldas2': source}]
    weights = {'nldas2': source}
    data = {'inputs': records(times, candidates, [None], {}),
            'static': assets(weights, source, source, None, None, None),
            'outputs': {times[0].isoformat(): identity(output)}}
    (tmp_path/'cache.json').write_text(json.dumps(data))
    args = (tmp_path, times, candidates, [None], weights, source, source)
    assert reuse(*args) == [output]
    with pytest.raises(ValueError, match='settings'):
        reuse(*args, mrms_quality_threshold=0.7)
    output.write_bytes(b'changed')
    with pytest.raises(ValueError, match='output changed'):
        reuse(*args)
    source.write_bytes(b'changed source')
    with pytest.raises(ValueError, match='inputs differ'):
        reuse(*args)
