import numpy as np
from netCDF4 import Dataset

from hydro_ops.download.stage4_revision_audit import compare_netcdf, sha256


def create(path, values, units='mm'):
    with Dataset(path, 'w') as ds:
        ds.createDimension('x', 3)
        var = ds.createVariable('precip', 'f4', ('x',), fill_value=-9999.)
        var.units = units
        var[:] = values


def test_comparison_masks_values_and_metadata(tmp_path):
    a, b = tmp_path / 'a.nc', tmp_path / 'b.nc'
    create(a, [1, 2, -9999])
    create(b, [1, 2, -9999])
    assert compare_netcdf(a, b) == {}
    create(b, [1, 3, 4])
    result = compare_netcdf(a, b)['precip']
    assert result == {'mask_changed_cells': 1, 'value_changed_cells': 1, 'max_abs_difference': 1.0}
    create(b, [1, 2, -9999], units='m')
    assert compare_netcdf(a, b)['precip']['schema_changed']


def test_encoding_only_and_nan(tmp_path):
    a, b = tmp_path / 'a.nc', tmp_path / 'b.nc'
    create(a, [1, np.nan, -9999])
    create(b, [1, np.nan, -9999])
    with Dataset(b, 'a') as ds:
        ds.history = 'different processing timestamp'
    assert sha256(a) != sha256(b)
    assert compare_netcdf(a, b) == {}


def test_existing_snapshot_creates_report_directory(tmp_path, monkeypatch):
    import importlib.util
    import json
    from dataclasses import replace
    from pathlib import Path

    from hydro_ops.config import load_settings

    script = Path(__file__).resolve().parents[1] / 'bin/audit_stage4_late_revisions.py'
    spec = importlib.util.spec_from_file_location('stage4_audit_cli', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = replace(load_settings(), project_root=tmp_path, work_root=tmp_path / 'work',
                       stage4_data_dir=tmp_path / 'operational')
    candidate = tmp_path / 'candidate'
    for day in ('20', '21', '22'):
        relative = Path(f'realtime/2026/09/{day}/st4_conus.202609{day}00.01h.grb2')
        for root in (candidate, settings.stage4_data_dir):
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b'identical fixture')
    monkeypatch.setattr(module, 'load_settings', lambda: settings)
    monkeypatch.setattr('sys.argv', [str(script), '--as-of', '2026-09-30',
                                    '--candidate-root', str(candidate)])
    module.main()
    result = json.loads(next((tmp_path / 'forcing/status/stage4-revisions').glob('*.json')).read_text())
    assert result['status'] == 'passed'
    assert result['counts']['identical_bytes'] == 3


def test_monitor_preserves_first_reference(tmp_path, monkeypatch):
    import importlib.util
    import json
    from dataclasses import replace
    from pathlib import Path
    from types import SimpleNamespace

    from hydro_ops.config import load_settings

    script = Path(__file__).resolve().parents[1] / 'bin/audit_stage4_late_revisions.py'
    spec = importlib.util.spec_from_file_location('monitor_cli', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = replace(load_settings(), project_root=tmp_path, work_root=tmp_path / 'work')
    payload = [b'first observation']
    class Downloader:
        def __init__(self, config):
            self.root = config.stage4_data_dir
        def discover_realtime(self, day):
            return [SimpleNamespace(destination=self.root / day.strftime('realtime/%Y/%m/%d')
                                    / f'st4_conus.{day:%Y%m%d}00.01h.grb2')]
        def download_one(self, item, refresh=False):
            item.destination.parent.mkdir(parents=True, exist_ok=True)
            item.destination.write_bytes(payload[0])
    monkeypatch.setattr(module, 'load_settings', lambda: settings)
    monkeypatch.setattr(module, 'Stage4Downloader', Downloader)
    monkeypatch.setattr('sys.argv', [str(script), '--monitor'])
    module.main()
    reference = next((settings.work_root / 'stage4-revision-monitor/references').rglob('*.grb2'))
    payload[0] = b'second observation'
    module.main()
    assert reference.read_bytes() == b'first observation'
    reports = list((tmp_path / 'forcing/status/stage4-revisions').glob('*.json'))
    assert len(reports) == 2
    assert all(json.loads(p.read_text())['counts']['no_reference'] == 3 for p in reports)
