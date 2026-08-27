from __future__ import annotations

import importlib.util
from pathlib import Path


def _script():
    path = Path(__file__).parents[1] / "bin/create_forcing_scenario_view.py"
    spec = importlib.util.spec_from_file_location("create_forcing_scenario_view", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scenario_view_hides_products_without_touching_sources(tmp_path: Path) -> None:
    module = _script()
    source = tmp_path / "source"
    for relative in ("static", *module.PRODUCT_PATHS.values()):
        (source / relative).mkdir(parents=True)
    destination = tmp_path / "scenario"

    report = module.create_view(source, destination, {"nldas2", "mrms_pass2"})

    assert report["hidden_products"] == ["mrms_pass2", "nldas2"]
    assert (destination / "data/static").is_symlink()
    assert not (destination / "data" / module.PRODUCT_PATHS["nldas2"]).exists()
    assert not (destination / "data" / module.PRODUCT_PATHS["mrms_pass2"]).exists()
    assert (destination / "data" / module.PRODUCT_PATHS["hrrr"]).is_symlink()
    assert (source / module.PRODUCT_PATHS["nldas2"]).is_dir()
