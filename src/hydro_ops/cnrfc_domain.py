"""Fail-closed CNRFC grid migration gate shared by cron submitters and runners."""
import hashlib
import json


def migration_state(root):
    path = root / 'nwm/status/cnrfc-domain-migration/state.json'
    return json.loads(path.read_text()) if path.exists() else {'blocked': False}


def require_available(root, *, migration_test=False):
    state = migration_state(root)
    if not state.get('blocked'):
        require_current_grid(root)
        return
    if migration_test and state.get('status') == 'promoted_pending_tests':
        manifest = json.loads((root / 'nwm/static/domains/cnrfc/manifest.json').read_text())
        if manifest.get('grid_version') == 'cnrfc_hu18_v1':
            require_current_grid(root)
            return
    raise RuntimeError('CNRFC grid migration in progress; normal operations are gated')


def require_current_grid(root):
    config = root / 'config/cnrfc_nrt_daily.json'
    expected = json.loads(config.read_text()) if config.exists() else {}
    if not expected.get('grid_version'):
        return
    static = root / 'nwm/static/domains/cnrfc'
    manifest = json.loads((static / 'manifest.json').read_text())
    if manifest.get('grid_version') != expected['grid_version']:
        raise RuntimeError('Canonical CNRFC grid version differs from operational configuration')
    for relative in ('masks/cnrfc_masks.nc', 'parameters/domain_masks.nc'):
        with (static / relative).open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != expected['mask_sha256']:
                raise RuntimeError('Canonical CNRFC operational mask checksum changed')


def require_legacy_grid(root):
    path = root / 'nwm/static/domains/cnrfc/manifest.json'
    if path.exists() and json.loads(path.read_text()).get('grid_version') == 'cnrfc_hu18_v1':
        raise RuntimeError('The expanded grid is canonical CNRFC; this legacy/candidate workflow is retired')
