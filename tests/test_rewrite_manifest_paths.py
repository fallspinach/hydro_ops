import importlib.util
from pathlib import Path


def load_script():
    path = Path(__file__).parents[1] / "bin/rewrite_manifest_paths.py"
    spec = importlib.util.spec_from_file_location("rewrite_manifest_paths", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rewrite_value_updates_absolute_and_relative_legacy_prefixes(tmp_path):
    module = load_script()
    old_absolute = str(tmp_path / "data/forcing/noaa/hrrr")
    value = {
        "absolute": old_absolute,
        "relative": "outputs/forcing/nwm/retro/1982",
        "canonical": "forcing/outputs/conus/retro/1982",
    }

    rewritten, count = module.rewrite_value(value, module.mappings(tmp_path))

    assert count == 2
    assert rewritten["absolute"] == str(tmp_path / "forcing/inputs/noaa/hrrr")
    assert rewritten["relative"] == "forcing/outputs/conus/retro/1982"
    assert rewritten["canonical"] == value["canonical"]


def test_atomic_write_preserves_file_mode(tmp_path):
    module = load_script()
    path = tmp_path / "sample.manifest.json"
    path.write_text("{}\n")
    path.chmod(0o640)

    module.atomic_write(path, '{"path": "forcing/inputs"}\n')

    assert path.read_text() == '{"path": "forcing/inputs"}\n'
    assert path.stat().st_mode & 0o777 == 0o640
