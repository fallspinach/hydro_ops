import gzip
import importlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from hydro_ops.cnrfc_domain import require_available, require_legacy_grid
from hydro_ops.download.cdec_fnf import write_json
from hydro_ops.forcing.cnrfc_sync import submit
from hydro_ops.wrf_hydro.nrt_schedule import submit_if_enabled


def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    return importlib.import_module('migrate_cnrfc_grid')


def test_migration_blocks_submitters_without_scheduler_calls(tmp_path, monkeypatch):
    write_json(tmp_path / 'nwm/status/cnrfc-domain-migration/state.json',
               {'status': 'preparing', 'blocked': True})
    monkeypatch.setattr('subprocess.check_output', lambda *_a, **_k: pytest.fail('Gated submission contacted scheduler'))
    assert submit(tmp_path) is None
    assert submit_if_enabled(tmp_path, ['123'])['status'] == 'disabled_cnrfc_grid_migration'
    with pytest.raises(RuntimeError, match='migration'):
        require_available(tmp_path, migration_test=True)
    write_json(tmp_path / 'nwm/static/domains/cnrfc/manifest.json', {'grid_version': 'cnrfc_hu18_v1'})
    write_json(tmp_path / 'nwm/status/cnrfc-domain-migration/state.json',
               {'status': 'promoted_pending_tests', 'blocked': True})
    require_available(tmp_path, migration_test=True)
    with pytest.raises(RuntimeError):
        require_available(tmp_path)
    with pytest.raises(RuntimeError, match='retired'):
        require_legacy_grid(tmp_path)


def test_path_translation_promotes_only_paths_and_keeps_version_identity(monkeypatch):
    module = tool(monkeypatch)
    old = '/project/nwm/outputs/cnrfc_hu18_v1/nrt/nrt_continuation_v1/hourly/2026/04/file'
    assert module.rewrite(old, module.path_mapping()) == '/project/nwm/outputs/cnrfc/nrt/hourly/2026/04/file'
    assert module.rewrite('cnrfc_hu18_v1', module.path_mapping()) == 'cnrfc_hu18_v1'
    asset = '/project/nwm/static/domains/cnrfc_hu18_v1/masks/cnrfc_hu18_masks.nc'
    assert module.rewrite(asset, module.path_mapping()) == '/project/nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'


def test_retirement_paths_reject_project_root_and_symlink_escape(tmp_path, monkeypatch):
    module = tool(monkeypatch)
    retired = tmp_path / module.WORK / 'retired'
    retired.mkdir(parents=True)
    with pytest.raises(ValueError, match='escapes'):
        module.retirement_path(tmp_path, 'nwm/static')
    outside = tmp_path / 'keep'
    outside.mkdir()
    os.symlink(outside, retired / 'escape')
    with pytest.raises(ValueError, match='escapes'):
        module.retirement_path(tmp_path, str((retired / 'escape/file').relative_to(tmp_path)))
    assert module.retirement_path(tmp_path, str((retired / 'file').relative_to(tmp_path))) == retired / 'file'


def test_enumeration_does_not_follow_external_symlinks(tmp_path, monkeypatch):
    module = tool(monkeypatch)
    old = tmp_path / 'old'
    old.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'precious').write_text('keep')
    os.symlink(outside, old / 'link')
    assert list(module.enumerate_files(old)) == [old / 'link']


def retirement_fixture(tmp_path, module):
    state = tmp_path / module.STATE
    write_json(state / 'state.json', {'status': 'promoted_pending_tests', 'blocked': True})
    write_json(tmp_path / 'forcing/status/cnrfc/sync-11/status.json', {'status': 'passed', 'failed': 0})
    write_json(tmp_path / 'nwm/runs/cnrfc/nrt/cycles/job-12/cycle.json',
        {'status': 'complete', 'test': True, 'rst_typ': 1, 'daily_checkpoint_continuation': 'passed'})
    files = []
    for name in ('old-a', 'old-b'):
        path = tmp_path / module.WORK / 'retired' / 'forcing/outputs/cnrfc' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        files.append({'path': str(path.relative_to(tmp_path)), 'identity': module.lidentity(path)})
    deletion = state / 'old-grid-deletion-plan.jsonl.gz'
    with gzip.open(deletion, 'wt') as handle:
        for row in files:
            handle.write(json.dumps(row) + '\n')
    archive = state / 'old-grid-provenance.tar.gz'
    archive.write_bytes(b'preserved provenance')
    write_json(state / 'plan.json', {'deletion_manifest_sha256': module.digest(deletion),
                                   'provenance_archive_sha256': module.digest(archive)})
    write_json(tmp_path / 'nwm/static/domains/cnrfc/approval.json', {})
    write_json(tmp_path / 'nwm/static/domains/cnrfc/manifest.json', {})
    return files


def test_retirement_prechecks_all_targets_before_unlink_and_restores_only_on_success(tmp_path, monkeypatch):
    module = tool(monkeypatch)
    files = retirement_fixture(tmp_path, module)
    monkeypatch.setattr(module, 'audit_expanded', lambda *_a, **_k: {'passed': True})
    second = tmp_path / files[1]['path']
    second.write_text('changed after audited deletion plan')
    with pytest.raises(ValueError, match='changed'):
        module.retire(tmp_path, '12', '11')
    assert (tmp_path / files[0]['path']).exists()
    assert json.loads((tmp_path / module.STATE / 'state.json').read_text())['blocked']


def test_retirement_keeps_provenance_and_other_domains(tmp_path, monkeypatch):
    module = tool(monkeypatch)
    files = retirement_fixture(tmp_path, module)
    monkeypatch.setattr(module, 'audit_expanded', lambda *_a, **_k: {'passed': True})
    unrelated = tmp_path / 'nwm/restarts/yampa/keep'
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text('untouched')
    module.retire(tmp_path, '12', '11')
    assert all(not (tmp_path / row['path']).exists() for row in files)
    assert unrelated.read_text() == 'untouched'
    assert (tmp_path / module.STATE / 'old-grid-provenance.tar.gz').exists()
    final = json.loads((tmp_path / module.STATE / 'state.json').read_text())
    assert final['status'] == 'complete' and not final['blocked']


def test_archive_audit_checks_receipts_coverage_and_restart_pairs(tmp_path, monkeypatch):
    module = tool(monkeypatch)
    output = tmp_path / 'nwm/outputs/cnrfc/nrt'
    first, last = datetime.fromisoformat('2026-04-18'), datetime.fromisoformat('2026-04-20')
    for day, records in [('20260418', 23), ('20260419', 24), ('20260420', 1)]:
        path = output / f'hourly/2026/04/{day}.CHRTOUT_DOMAIN1'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'model values')
        write_json(path.with_name(path.name + '.archive.json'), {'status': 'passed',
            'all_variable_values_verified': True, 'published_identity': module.identity(path), 'records': records})
        if day != '20260420':
            for kind in ('LDASOUT', 'CHRTOUT'):
                path = output / f'daily/2026/04/{day}.{kind}_DOMAIN1.daily'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'daily')
    for name in module.restart_names(last):
        path = tmp_path / 'nwm/restarts/cnrfc/nrt/2026/04' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'checkpoint')
    marker = tmp_path / 'nwm/runs/cnrfc/nrt/campaign/20260418-20260420/accepted.json'
    write_json(marker, {'passed': True, 'start': str(first), 'end': str(last),
                       'hourly_channel_records': 48, 'daily_land_files': 2, 'daily_channel_files': 2})
    assert module.archive_audit(tmp_path, 'cnrfc', 'nrt', 'campaign', first, last, promoted=True)['days'] == 2
    (output / 'hourly/2026/04/20260419.CHRTOUT_DOMAIN1').write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='Stale'):
        module.archive_audit(tmp_path, 'cnrfc', 'nrt', 'campaign', first, last, promoted=True)
