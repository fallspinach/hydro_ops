from pathlib import Path

from hydro_ops.config import find_project_root, load_settings


def test_find_project_root_from_nested_directory():
    root = Path(__file__).resolve().parents[1]
    assert find_project_root(root / "src" / "hydro_ops") == root


def test_settings_resolve_relative_paths(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("HYDRO_OPS_PROJECT_ROOT", str(root))
    settings = load_settings()
    assert settings.project_root == root
    assert settings.data_root == root / "data"
    assert settings.nldas_data_dir.is_absolute()
