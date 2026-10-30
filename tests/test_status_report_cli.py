import importlib.util
import json
from pathlib import Path

import pytest


def cli(monkeypatch, arguments):
    path = Path(__file__).resolve().parents[1] / 'bin/report_forcing_status.py'
    spec = importlib.util.spec_from_file_location('status_report_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr('sys.argv', [str(path), *arguments])
    def forbidden(*args, **kwargs):
        raise AssertionError('Saved report must not scan or load settings')
    monkeypatch.setattr(module, 'build_status', forbidden)
    monkeypatch.setattr(module, 'load_settings', forbidden)
    return module


def report():
    return {'generated_at_utc': '2026-09-28T00:00:00+00:00',
            'summary': {'status': 'ok', 'issues': []}, 'external_sources': [],
            'domains': {}, 'slurm': {'available': False, 'skipped': True}}


def test_saved_text_default_path(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'forcing/status/forcing-status.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report()))
    module = cli(monkeypatch, ['--read-report'])
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'bin/report_forcing_status.py'))
    assert module.main() == 0
    result = capsys.readouterr().out
    assert '2026-09-28T00:00:00+00:00' in result
    assert 'Overall: OK' in result


def test_saved_json_and_output(tmp_path, monkeypatch):
    source, destination = tmp_path / 'input.json', tmp_path / 'out.json'
    source.write_text(json.dumps(report()))
    before = source.read_bytes()
    module = cli(monkeypatch, ['--read-report', str(source), '--format', 'json',
                               '--output', str(destination)])
    assert module.main() == 0
    assert json.loads(destination.read_text()) == report()
    assert source.read_bytes() == before


@pytest.mark.parametrize('content', [None, 'broken', '[]', '{}'])
def test_bad_saved_report(tmp_path, monkeypatch, content):
    path = tmp_path / 'report.json'
    if content is not None:
        path.write_text(content)
    module = cli(monkeypatch, ['--read-report', str(path)])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


@pytest.mark.parametrize('extra', [['--start', '2026-09-01'], ['--gap-limit', '20'],
                                  ['--no-slurm'], ['--output', 'report.json']])
def test_conflicting_options(monkeypatch, extra):
    module = cli(monkeypatch, ['--read-report', 'report.json', *extra])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
